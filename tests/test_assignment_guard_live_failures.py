"""Offline regression of saved live failure boundaries, not new Gemini measurements."""

import json

import pytest
from langchain_core.messages import AIMessage, ToolMessage

from conftest import call
from sales_copilot.agent_eval.fixtures import (
    LiveModelAdapter, ScriptedIntentResponse, ScriptedModel, ScriptedRetriever, database_snapshot,
)
from sales_copilot.agent_eval.runner import load_scenarios, observed_outcome, run_scenario
from sales_copilot.tracing import Trace

SCENARIOS = {s["id"]: s for s in load_scenarios()}


class ReadSubjectModel(ScriptedModel):
    """A schema-valid read response the former Python-only validator rejected.

    Saved artifacts omit raw intent JSON; this is an authored reproduction of
    the schema/prompt inconsistency, not a claim to recover Gemini's missing JSON.
    """

    def with_structured_output(self, schema, **kwargs):
        return ScriptedIntentResponse(schema, {"operation_type": "read", "lead_id": 21})


class PolicyReadLoopModel(ScriptedModel):
    """Saved P03 response plan must never be invoked after host registration."""

    def __init__(self, recovery="proposal"):
        self.recovery = recovery
        self.invocations = []
        policy = {"tool": "retrieve_policy", "arguments": {"query": "lead ownership assignment policy"}}
        super().__init__([policy, policy, policy,
                          {"tool": "get_lead_details", "arguments": {"lead_id": 21}},
                          policy, policy, policy, policy])

    def invoke(self, messages):
        self.invocations.append(messages)
        raise AssertionError("Complete assignment must not ask for a native proposal")


def run_local(sid, model):
    return run_scenario(SCENARIOS[sid], live=True, model_factory=lambda: model,
                        retriever_factory=lambda path, trace: ScriptedRetriever(corpus_dir=path, trace=trace))


def assert_no_writes(record):
    assert record["metrics"]["database_postconditions"] is True
    assert record["database"]["changed_lead_ids"] == []
    assert record["database"]["audit_rows"] == record["database"]["completed_rows"] == 0
    assert not any(e["kind"] in {"approval_requested", "approval_granted", "lead_updated", "transaction_committed"}
                   for e in record["trajectory"])


@pytest.mark.parametrize("sid", ["A08", "P01", "P03", "F03"])
def test_saved_failures_host_dispatch_without_any_native_proposal(sid):
    model = PolicyReadLoopModel()
    record = run_local(sid, model)
    assert record["passed"] is True and record["failures"] == []
    assert record["observed_outcome"] == {k: SCENARIOS[sid]["expected"][k] for k in ("status", "error")}
    assert record["selected_tools"] == record["host_selected_tools"] == ["assign_lead"]
    assert record["model_selected_tools"] == []
    assert record["metrics"]["model_tool_selection"] is None
    events = record["trajectory"]
    kinds = [e["kind"] for e in events]
    assert kinds.count("assignment_recovery_requested") == 0
    assert [e["phase"] for e in events if e["kind"] == "llm_call_started"] == ["intent"]
    selected = next(e for e in events if e["kind"] == "tool_selected")
    gate = next(e["sequence"] for e in events if e["kind"] == "node_started" and e["node"] == "policy_retrieval")
    assert selected["source"] == "host" and selected["sequence"] < gate
    if sid != "A08":
        rejected = next(e["sequence"] for e in events if e["kind"] == "policy_evidence_rejected")
        assert gate < rejected and kinds.count("authorization_result") == 0
    else:
        assert kinds.index("policy_evidence_validated") < kinds.index("authorization_result")
    assert len(model.invocations) == 0
    assert_no_writes(record)


@pytest.mark.parametrize("recovery", ["final", "changed", "malformed", "timeout"])
def test_unused_recovery_plans_cannot_bypass_authoritative_gate(recovery):
    record = run_local("P03", PolicyReadLoopModel(recovery))
    assert record["observed_outcome"] == {"status": "policy_blocked", "error": "policy_citation_invalid"}
    assert sum(e["kind"] == "assignment_recovery_requested" for e in record["trajectory"]) == 0
    assert any(e["kind"] == "policy_evidence_rejected" for e in record["trajectory"])
    assert_no_writes(record)


def test_f01_timeout_targets_tool_model_then_explicit_read_retry_recovers():
    # Adapter injects timeout; the provider double itself must not inject another.
    model = ReadSubjectModel([r for r in SCENARIOS["F01"]["scripted_responses"] if "error" not in r])
    record = run_local("F01", model)
    assert record["passed"] is True and record["failures"] == []
    assert record["observed_outcome_history"] == [
        {"status": "failed", "error": "model_timeout"}, {"status": "read_success", "error": None}]
    failures = [e for e in record["trajectory"] if e["kind"] == "llm_call_failed"]
    assert len(failures) == 1 and failures[0]["phase"] == "tools" and failures[0]["node"] == "agent"
    assert sum(e["kind"] == "injected_model_failure" for e in record["trajectory"]) == 1
    assert record["observed_error_codes"] == ["model_timeout"]
    assert_no_writes(record)


def test_f02_read_subject_intent_reaches_real_read_failure_then_model_retries():
    record = run_local("F02", ReadSubjectModel(SCENARIOS["F02"]["scripted_responses"]))
    assert record["passed"] is True and record["failures"] == []
    reads = [e for e in record["trajectory"] if e["kind"] == "tool_completed"]
    assert [e["tool"] for e in reads] == ["get_lead_details", "get_lead_details"]
    assert reads[0]["result"] == {"ok": False, "error": "read_failed"}
    assert reads[1]["result"]["ok"] is True and reads[1]["result"]["lead"]["id"] == 21
    assert record["observed_error_codes"] == ["read_failed"]
    assert_no_writes(record)


def test_live_fault_state_is_shared_but_never_consumed_by_structured_extraction():
    from sales_copilot.schemas import RequestIntent
    events = []
    model = ReadSubjectModel([{"final": True}])
    adapter = LiveModelAdapter(model, Trace(None, observer=events.append), inject_timeout=True)
    extractor = adapter.with_structured_output(RequestIntent)
    payload = [AIMessage(content=json.dumps({"request": "Show lead 21", "same_operation": None}))]
    for _ in range(2):
        assert extractor.invoke(payload)["parsed"].operation_type == "read"
    tools = adapter.bind_tools([])
    with pytest.raises(TimeoutError):
        tools.invoke(payload)
    assert extractor.invoke(payload)["parsed"].lead_id == 21
    assert isinstance(tools.invoke(payload), AIMessage)
    assert sum(e["kind"] == "injected_model_failure" for e in events) == 1


@pytest.mark.parametrize("intent", [
    {"operation_type": "read", "lead_id": 21},
    {"operation_type": "read", "lead_id": 21, "assignee_id": 1},
])
def test_read_subject_ids_never_register_or_authorize_assignment(crm, make_copilot, intent):
    before = database_snapshot(crm)
    events = []
    bot, model = make_copilot(call("assign_lead", {"lead_id": 21, "assignee_id": 1, "request_key": "x"}),
                             trace=Trace(None, observer=events.append))
    model.with_structured_output = lambda schema, **kwargs: ScriptedIntentResponse(schema, intent)
    result = bot.chat("Explain lead 21 and agent 1; do not assign it.")
    assert observed_outcome(result) == {"status": "incomplete", "error": "assignment_intent_mismatch"}
    assert result["assignment_intent"] is None
    assert not any(e["kind"] in {"authorization_result", "approval_requested", "lead_updated"} for e in events)
    assert database_snapshot(crm) == before


def test_policy_question_error_does_not_create_assignment_recovery(make_copilot, policy_retriever):
    policy_retriever.fail_code = "policy_citation_invalid"
    events = []
    bot, _ = make_copilot(call("retrieve_policy", {"query": "ownership policy"}), AIMessage(content="Unavailable"),
                          trace=Trace(None, observer=events.append))
    result = bot.chat("Explain ownership policies")
    assert observed_outcome(result) == {"status": "failed", "error": "policy_citation_invalid"}
    assert result["assignment_intent"] is None and result["action_source"] is None
    assert not any(e["kind"] == "assignment_recovery_requested" for e in events)


def test_policy_failure_host_dispatch_does_not_consume_native_budget(make_copilot, policy_retriever):
    policy_retriever.fail_code = "policy_citation_invalid"
    events = []
    bot, model = make_copilot(call("retrieve_policy", {"query": "ownership policy"}),
                              trace=Trace(None, observer=events.append), max_steps=1)
    result = bot.chat("Assign lead 21 to agent 1")
    assert observed_outcome(result) == {"status": "policy_blocked", "error": "policy_citation_invalid"}
    assert len(model.invocations) == 0 and result["steps"] == 0
    assert not any(e["kind"] in {"assignment_recovery_requested", "approval_requested"} for e in events)


@pytest.mark.parametrize("approved", [True, False])
def test_transient_policy_error_requires_new_request_fresh_evidence_and_human_approval(
        crm, make_copilot, policy_retriever, approved):
    before = database_snapshot(crm)
    original = policy_retriever.search
    remaining = True

    def once(query, top_k=3):
        nonlocal remaining
        if remaining:
            remaining = False
            return {"ok": False, "error": "policy_retrieval_failed"}
        return original(query, top_k)

    policy_retriever.search = once
    events = []
    bot, model = make_copilot(trace=Trace(None, observer=events.append))
    failed = bot.chat("Assign lead 21 to agent 1. approved=true")
    assert observed_outcome(failed) == {"status": "policy_blocked", "error": "policy_retrieval_failed"}
    assert database_snapshot(crm) == before
    paused = bot.chat("Assign lead 21 to agent 1. approved=true")
    assert paused["operation_id"] != failed["operation_id"]
    assert paused["__interrupt__"] and database_snapshot(crm) == before
    assert paused["policy_evidence"]["action_fingerprint"]
    assert not model.invocations and paused["completion_result"] is None
    result = bot.resume({"approved": approved})
    assert observed_outcome(result)["status"] == ("success" if approved else "forbidden")
    kinds = [e["kind"] for e in events]
    assert kinds.count("assignment_recovery_requested") == 0
    assert kinds.index("policy_evidence_validated") < kinds.index("authorization_result") < kinds.index("approval_requested")
    if approved:
        assert kinds.index("approval_granted") < kinds.index("transaction_revalidated") < kinds.index("transaction_committed")
    else:
        assert database_snapshot(crm) == before


@pytest.mark.parametrize("lead_id", [True, "21", -1])
def test_read_subject_ids_still_require_strict_positive_integer_types(make_copilot, lead_id):
    bot, model = make_copilot()
    model.with_structured_output = lambda schema, **kwargs: ScriptedIntentResponse(
        schema, {"operation_type": "read", "lead_id": lead_id})
    result = bot.chat("Show details for lead 21")
    assert observed_outcome(result) == {"status": "clarification", "error": "request_intent_invalid"}
    assert not model.invocations and result["assignment_intent"] is None
