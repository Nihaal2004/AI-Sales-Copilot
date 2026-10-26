"""Opt-in, content-free LangSmith spans. Never serialize application objects.

Native LangChain tracing is suppressed at execution boundaries because graph state,
tool payloads and exception text can contain private CRM data and consent tokens.
"""

import os
import time
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime, timezone
from functools import lru_cache, wraps
from uuid import uuid4

from langsmith.run_helpers import tracing_context


_parent = ContextVar("sales_copilot_span", default=None)
_local_only = ContextVar("sales_copilot_local_only", default=False)
_NODES = {"begin", "agent", "routing", "read_tools", "policy_retrieval", "authorization",
          "approval", "mutation", "finish"}
_TOOLS = {"search_leads", "get_lead_details", "retrieve_policy", "assign_lead"}
_NAMES = ({"graph.invoke", "graph.stream", "graph.ainvoke", "graph.astream",
           "llm.intent", "llm.tools", "retrieval.rank", "retrieval.search", "retrieval.prepare"}
          | {"node." + n for n in _NODES} | {"tool." + n for n in _TOOLS})


class LangSmithExporter:
    def __init__(self, client, project):
        self.client, self.project = client, project
        self.export_errors = 0

    def send(self, record):
        try:
            self.client.create_run(project_name=self.project, **record)
        except Exception:
            # Separate from Trace.observer_errors: remote availability cannot
            # change an evaluation grade, approval, transaction or retry.
            self.export_errors += 1


@lru_cache(maxsize=4)
def _configured_exporter(api_key, endpoint, project):
    from langsmith import Client

    return LangSmithExporter(Client(api_key=api_key, api_url=endpoint or None,
                                    hide_inputs=True, auto_batch_tracing=True,
                                    omit_traced_runtime_info=True, timeout_ms=1000), project)


def get_exporter():
    if _local_only.get() or os.getenv("SALES_COPILOT_LANGSMITH", "").lower() != "true":
        return None
    key = os.getenv("LANGSMITH_API_KEY", "").strip()
    if not key:
        return None
    try:
        return _configured_exporter(key, os.getenv("LANGSMITH_ENDPOINT", ""),
                                    os.getenv("LANGSMITH_PROJECT", "sales-copilot"))
    except Exception:
        return None


@contextmanager
def local_only():
    """Evaluation never exports telemetry, even in a tracing-enabled shell."""
    token = _local_only.set(True)
    try:
        with tracing_context(enabled=False, parent=False):
            yield
    finally:
        _local_only.reset(token)


class Span:
    def __init__(self):
        self.usage = None
        self.failed = False
        self.interrupted = False

    def result(self, result):
        # Do not retain the result, even temporarily in an exporter queue.
        self.failed = isinstance(result, dict) and result.get("ok") is False
        self.interrupted = isinstance(result, dict) and bool(result.get("__interrupt__"))


@contextmanager
def span(name, run_type="chain"):
    """Only static operation names, timing, status and integer tokens may leave.

    Explicit SDK calls export our own records even though native auto-tracing is
    disabled. No prompts, outputs, IDs, config, tracebacks or event payloads enter
    the SDK. ContextVar nesting propagates through LangGraph's worker context.
    """
    parent = _parent.get()
    exporter = None if _local_only.get() else parent["exporter"] if parent else get_exporter()
    state = Span()
    started = time.perf_counter()
    start_time = datetime.now(timezone.utc)
    run_id = uuid4()
    order = start_time.strftime("%Y%m%dT%H%M%S%fZ") + str(run_id)
    current = {"exporter": exporter, "id": run_id,
               "trace_id": parent["trace_id"] if parent else run_id,
               "dotted_order": parent["dotted_order"] + "." + order if parent else order}
    token = _parent.set(current)
    try:
        with tracing_context(enabled=False, parent=False):
            try:
                yield state
            except BaseException as exc:
                from langgraph.errors import GraphInterrupt

                state.interrupted = isinstance(exc, (GraphInterrupt, GeneratorExit))
                state.failed = not state.interrupted
                raise
    finally:
        _parent.reset(token)
        if exporter is not None:
            metadata = {"duration_ms": round((time.perf_counter() - started) * 1000, 3),
                        "status": "interrupted" if state.interrupted else "error" if state.failed else "ok"}
            usage = state.usage
            usage = ({k: usage[k] for k in ("input_tokens", "output_tokens", "total_tokens")
                      if type(usage.get(k)) is int and usage[k] >= 0} if isinstance(usage, dict) else {})
            record = {"id": run_id, "trace_id": current["trace_id"],
                      "dotted_order": current["dotted_order"],
                      "name": name if name in _NAMES else "operation",
                      "run_type": run_type if run_type in {"chain", "llm", "tool", "retriever"} else "chain",
                      "start_time": start_time, "end_time": datetime.now(timezone.utc),
                      "inputs": {}, "outputs": {"usage_metadata": usage} if usage else {},
                      "extra": {"metadata": metadata},
                      "error": "operation_failed" if state.failed else None}
            if parent:
                record["parent_run_id"] = parent["id"]
            # A test/injected exporter must be just as unable to break execution.
            try:
                exporter.send(record)
            except Exception:
                pass


def observed(name, run_type="chain"):
    def decorate(function):
        @wraps(function)
        def wrapped(*args, **kwargs):
            with span(name, run_type) as current:
                result = function(*args, **kwargs)
                current.result(result)
                return result
        return wrapped
    return decorate


class ObservedGraph:
    """Execution boundary around the unchanged compiled StateGraph.

    State/checkpoint inspection delegates directly. Both invocation and streaming
    suppress native payload capture, including before the first node executes.
    """

    def __init__(self, graph):
        self._graph = graph

    def __getattr__(self, name):
        return getattr(self._graph, name)

    @observed("graph.invoke")
    def invoke(self, *args, **kwargs):
        return self._graph.invoke(*args, **kwargs)

    def stream(self, *args, **kwargs):
        with span("graph.stream"):
            yield from self._graph.stream(*args, **kwargs)

    async def ainvoke(self, *args, **kwargs):
        with span("graph.ainvoke"):
            return await self._graph.ainvoke(*args, **kwargs)

    async def astream(self, *args, **kwargs):
        with span("graph.astream"):
            async for item in self._graph.astream(*args, **kwargs):
                yield item

    @observed("graph.invoke")
    def batch(self, *args, **kwargs):
        return self._graph.batch(*args, **kwargs)

    async def abatch(self, *args, **kwargs):
        with span("graph.ainvoke"):
            return await self._graph.abatch(*args, **kwargs)

    async def astream_events(self, *args, **kwargs):
        with span("graph.astream"):
            async for item in self._graph.astream_events(*args, **kwargs):
                yield item

    def with_config(self, *args, **kwargs):
        return ObservedGraph(self._graph.with_config(*args, **kwargs))
