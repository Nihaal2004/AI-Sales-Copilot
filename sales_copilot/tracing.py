"""Local JSON traces; no raw prompts, provider responses, secrets or contact data."""

import json
import hashlib
import time
from functools import wraps
from collections.abc import Callable
from uuid import uuid4

from .observability import observed as remote_observed

SAFE_FIELDS = {"ok", "error", "lead", "leads", "lead_id", "id", "assignee_id", "owner_id",
               "previous_owner_id", "city", "status", "score", "priority", "created_at", "updated_at",
               "approved", "count", "unassigned_only", "http_status", "hint", "message_type", "response_kind",
               "has_text", "answer_source", "model_final_received", "model_final_has_text",
               "stage", "ranking", "citation", "score", "policy_refs", "policy_id", "title", "version",
               "section_id", "section_title", "chunk_id", "text_sha256", "dense_score", "bm25_score",
               "rrf_score", "cross_score", "notice", "corpus_fingerprint", "actor_id",
               "operation_type", "active_count", "capacity_limit", "retry_same_operation", "rule_version",
               "sort_by", "sort_order", "strategy", "factors", "field", "weight", "tie_breakers", "direction"}

EVENT_FIELDS = {"from_node", "to_node", "phase", "usage", "duration_ms", "tool_call_count", "rowcount",
                "displayed_lead_ids", "result_statuses", "route", "approved", "mode", "trial", "approval_source",
                "database", "expected_action", "source", "fault", "error_type", "provider",
                "attempt", "delay_seconds", "http_status", "quota_types", "daily_exhausted",
                "retry_after_seconds", "availability", "prerequisite"}


def observation_redact(value):
    safe = redact(value)
    if isinstance(safe, dict):
        for key in ("actor_id", "hint"):
            if key in safe:
                safe[key] = "<redacted>"
        if "city" in safe and safe["city"] is not None:
            cities = {c.casefold(): c for c in ("Chennai", "Bangalore", "Hyderabad")}
            safe["city"] = cities.get(str(safe["city"]).casefold(), "<redacted>")
        return {k: observation_redact(v) if isinstance(v, (dict, list)) else v for k, v in safe.items()}
    if isinstance(safe, list):
        return [observation_redact(item) for item in safe]
    return safe


def redact(value):
    if isinstance(value, dict):
        return {key: redact(item) if key in SAFE_FIELDS else "<redacted>" for key, item in value.items()}
    if isinstance(value, list):
        return [redact(item) for item in value]
    return value


class Trace:
    def __init__(self, sink: Callable[[str], None] | None = print, *, observer=None, correlation_id=None):
        self.sink = sink
        self.observer = observer
        self.correlation_id = correlation_id or str(uuid4())
        self.operation_ref = None
        self.observer_errors = 0
        self._sequence = 0
        self._started = time.perf_counter()
        self._last_node = None

    def operation(self, key):
        # Request keys can contain arbitrary host data: only an opaque digest is recorded.
        self.operation_ref = hashlib.sha256(str(key).encode()).hexdigest()

    def event(self, kind, *, node=None, tool=None, arguments=None, result=None, **fields):
        """Structured observation, independent of printable logs. Never controls execution."""
        if self.observer is None:
            return
        self._sequence += 1
        event = {"schema_version": 1, "correlation_id": self.correlation_id,
                 "operation_ref": self.operation_ref, "sequence": self._sequence,
                 "elapsed_ms": round((time.perf_counter() - self._started) * 1000, 3), "kind": kind}
        if node:
            event["node"] = node
        if tool:
            event["tool"] = tool
        if arguments is not None:
            event["arguments"] = observation_redact(arguments)
        if result is not None:
            # Never retain free-form policy bodies, model prose, exceptions or contacts.
            event["result"] = observation_redact(result)
        event.update({k: v if k in EVENT_FIELDS else "<redacted>" for k, v in fields.items()})
        try:
            self.observer(event)
        except Exception:
            # A broken telemetry consumer must never change a commit or approval.
            # Evaluations explicitly fail if this counter is nonzero.
            self.observer_errors += 1

    def node(self, name, function):
        """Observe actual node invocations and resume re-entry, not predicted routing."""
        @wraps(function)
        @remote_observed("node." + name)
        def observed(state):
            from langgraph.errors import GraphInterrupt
            if name == "begin":
                self._last_node = None
                if state.get("operation_id"):
                    self.operation(state["operation_id"])
            if self._last_node is not None:
                self.event("node_transition", from_node=self._last_node, to_node=name)
            self._last_node = name
            self.event("node_started", node=name)
            try:
                result = function(state)
            except GraphInterrupt:
                self.event("node_interrupted", node=name)
                raise
            except Exception:
                self.event("node_failed", node=name)
                raise
            self.event("node_completed", node=name, route=result.get("route"))
            return result
        return observed

    def emit(self, node: str, *, tool: str | None = None, arguments=None, result=None, source=None):
        event = {"node": node}
        if source is not None:
            event["source"] = source
        if tool is not None:
            event["tool"] = tool
        if arguments is not None:
            event["arguments"] = redact(arguments)
        if result is not None:
            event["result"] = redact(result)
        if self.sink:
            self.sink("TRACE " + json.dumps(event, ensure_ascii=False))
