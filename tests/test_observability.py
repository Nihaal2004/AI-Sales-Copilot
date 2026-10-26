"""Exercise telemetry with adversarial payloads; never contact LangSmith."""

import asyncio
import json

import pytest
from langchain_core.messages import AIMessage, HumanMessage
from langsmith.run_helpers import get_tracing_context

from sales_copilot import observability as obs
from sales_copilot.graph import build_graph
from sales_copilot.intent import invoke_observed
from sales_copilot.tracing import Trace
from conftest import ScriptedModel, call


class RecordingExporter:
    def __init__(self):
        self.records = []

    def send(self, record):
        self.records.append(record)


@pytest.fixture
def exported(monkeypatch):
    exporter = RecordingExporter()
    monkeypatch.setattr(obs, "get_exporter", lambda: exporter)
    return exporter.records


def test_opt_in_needs_application_flag_and_credentials(monkeypatch):
    created = []
    monkeypatch.setattr(obs, "_configured_exporter", lambda *args: created.append(args) or "exporter")
    monkeypatch.setenv("LANGSMITH_TRACING", "true")
    monkeypatch.setenv("LANGSMITH_API_KEY", "test-credential")
    monkeypatch.delenv("SALES_COPILOT_LANGSMITH", raising=False)
    assert obs.get_exporter() is None
    monkeypatch.setenv("SALES_COPILOT_LANGSMITH", "true")
    monkeypatch.delenv("LANGSMITH_API_KEY")
    assert obs.get_exporter() is None
    assert not created
    monkeypatch.setenv("LANGSMITH_API_KEY", "test-credential")
    assert obs.get_exporter() == "exporter"


def test_graph_paths_usage_and_tools_export_no_crm_or_model_content(make_copilot, exported, monkeypatch):
    secret = "PRIVATE-CREDENTIAL-APPROVAL-TOKEN-CRM-EMAIL"
    monkeypatch.setenv("LANGSMITH_TRACING", "true")
    monkeypatch.setenv("LANGSMITH_API_KEY", secret)
    native = []
    monkeypatch.setattr("langsmith.Client.create_run", lambda *args, **kwargs: native.append(kwargs))
    events, printable = [], []
    trace = Trace(printable.append, observer=events.append, correlation_id=secret)
    bot, model = make_copilot(call("get_lead_details", {"lead_id": 1}),
                             AIMessage(content=secret, usage_metadata={"input_tokens": 12,
                                       "output_tokens": 3, "total_tokens": 15}), trace=trace)
    result = bot.chat("Show lead 1. " + secret, operation_id=secret)
    assert result["answer"] and events and printable
    assert trace.observer_errors == 0
    names = {r["name"] for r in exported}
    assert {"graph.invoke", "node.begin", "node.agent", "node.read_tools", "llm.intent",
            "llm.tools", "tool.get_lead_details"} <= names
    serialized = json.dumps(exported, default=str)
    assert secret not in serialized
    assert "lead_id" not in serialized and "messages" not in serialized
    assert all(r["inputs"] == {} for r in exported)
    assert any(r["outputs"].get("usage_metadata", {}).get("total_tokens") == 15 for r in exported)
    roots = [r for r in exported if "parent_run_id" not in r]
    assert len(roots) == 1 and roots[0]["name"] == "graph.invoke"
    ids = {r["id"] for r in exported}
    assert all(r["parent_run_id"] in ids for r in exported if "parent_run_id" in r)
    assert all(r["trace_id"] == roots[0]["id"] for r in exported)
    assert not native  # Native callbacks must never receive raw graph/model/tool payloads.


def test_approval_interrupt_and_commit_keep_identical_behavior(make_copilot, exported):
    bot, model = make_copilot()
    pending = bot.chat("Assign lead 21 to agent 1")
    assert pending["__interrupt__"]
    root = next(r for r in exported if r["name"] == "graph.invoke")
    assert root["extra"]["metadata"]["status"] == "interrupted"
    assert bot.resume({"approved": True})["results"][-1]["result"]["ok"]
    assert "tool.assign_lead" in {r["name"] for r in exported}
    wire = json.dumps(exported, default=str)
    assert "approval_binding" not in wire and "consent" not in wire


def test_export_failure_cannot_change_commit_or_custom_observer(make_copilot, monkeypatch):
    class Broken:
        def send(self, record):
            raise RuntimeError("secret exporter error")
    monkeypatch.setattr(obs, "get_exporter", lambda: Broken())
    events = []
    trace = Trace(None, observer=events.append)
    bot, _ = make_copilot(trace=trace)
    assert bot.chat("Assign lead 21 to agent 1")["__interrupt__"]
    result = bot.resume({"approved": True})
    assert result["results"][-1]["result"]["ok"]
    assert events and trace.observer_errors == 0


def test_errors_and_non_integer_usage_never_export_free_text(exported):
    trace = Trace(None)
    with pytest.raises(RuntimeError):
        invoke_observed(ScriptedModel([RuntimeError("secret exception with CRM body")]), [], trace, node="agent")
    assert exported[-1]["error"] == "operation_failed"
    with obs.span("attacker-controlled-name", "attacker-controlled-type") as current:
        current.usage = {"input_tokens": True, "output_tokens": "secret", "total_tokens": -1,
                         "private": "secret"}
    wire = json.dumps(exported, default=str)
    assert "secret" not in wire and "attacker" not in wire
    assert exported[-1]["outputs"] == {}


def test_local_evaluation_suppresses_even_explicit_exporter(exported):
    with obs.local_only(), obs.span("retrieval.rank", "retriever"):
        assert get_tracing_context()["enabled"] is False
    assert not exported


def test_direct_stream_and_async_graph_invocation_are_private(crm, policy_retriever, exported):
    graph = build_graph(crm, ScriptedModel([AIMessage(content="read only")]),
                        policy_retriever=policy_retriever, trace=Trace(None))
    configured = graph.with_config({"configurable": {"thread_id": "private-thread"}})
    assert list(configured.stream({"messages": [HumanMessage(content="Hello")]}))
    assert any(r["name"] == "graph.stream" for r in exported)
    graph = build_graph(crm, ScriptedModel([AIMessage(content="read only")]),
                        policy_retriever=policy_retriever, trace=Trace(None))
    result = asyncio.run(graph.ainvoke({"messages": [HumanMessage(content="Hello")]},
                                      {"configurable": {"thread_id": "private-thread"}}))
    assert result["answer"]
    assert any(r["name"] == "graph.ainvoke" for r in exported)


def test_real_sdk_transformation_adds_no_environment_or_payload(monkeypatch):
    from langsmith import Client

    sent = []
    monkeypatch.setenv("LANGCHAIN_PRIVATE_CRM", "secret-env-data")
    monkeypatch.setattr(Client, "_create_run", lambda self, record, **kwargs: sent.append(record))
    client = Client(api_key="fake-key", auto_batch_tracing=False,
                    hide_inputs=True, omit_traced_runtime_info=True)
    exporter = obs.LangSmithExporter(client, "test-project")
    monkeypatch.setattr(obs, "get_exporter", lambda: exporter)
    with obs.span("llm.tools", "llm") as current:
        current.usage = {"input_tokens": 7, "output_tokens": 2, "total_tokens": 9}
    assert sent and exporter.export_errors == 0
    assert "secret-env-data" not in json.dumps(sent, default=str)
    assert sent[0]["inputs"] == {}
    assert sent[0]["outputs"]["usage_metadata"]["total_tokens"] == 9
    assert set(sent[0]["extra"]) == {"metadata"}
