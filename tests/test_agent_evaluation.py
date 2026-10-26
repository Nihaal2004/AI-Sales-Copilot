"""Stage 4 evaluates real graph/SQLite behavior and strict requested ordering."""

import copy
import json
from collections import Counter
from unittest.mock import patch

import pytest
from sales_copilot.agent_eval.fixtures import ScriptedModel, ScriptedRetriever
from sales_copilot.agent_eval.fixtures import database_snapshot, sql
from sales_copilot.agent_eval.grading import database_grade, grade, order_satisfied, safety_violations
from sales_copilot.agent_eval.runner import aggregate, load_scenarios, run_evaluation, run_scenario
from sales_copilot.tracing import Trace

SCENARIOS = load_scenarios()


@pytest.fixture(scope="module")
def evaluated():
    # Enforce the absence of provider calls AND local neural-model loading.
    with patch("sales_copilot.agent_eval.runner.create_model", side_effect=AssertionError("No live API in pytest")), \
         patch("sales_copilot.retrieval.PolicyRetriever.prepare", side_effect=AssertionError("No neural downloads in pytest")):
        return {s["id"]: run_scenario(s, run_id="pytest-scripted") for s in SCENARIOS}


@pytest.mark.parametrize("sid", [s["id"] for s in SCENARIOS])
def test_all_labelled_scenarios_exercise_real_graph_and_sqlite(evaluated, sid):
    result = evaluated[sid]
    assert result["passed"] is True
    assert result["metrics"]["database_postconditions"] is True
    assert result["metrics"]["trajectory_safety"] is True
    assert result["metrics"]["required_step_coverage"] == 1
    assert result["provider_adapter_invocations"] == 0
    assert result["token_usage"] == {"input_tokens": None, "output_tokens": None, "total_tokens": None}
    assert result["observer_errors"] == 0
    assert result["execution_exception"] is None
    if sid in {"R06", "R07"}:
        assert result["metrics"]["ranking_correct"] is True
        assert result["metrics"]["ranked_search_correct"] is True
    assert result["failures"] == []


def test_report_aggregation_and_applicable_denominators(evaluated):
    summary = aggregate(list(evaluated.values()))
    assert summary["fully_passing_trials"] == 30
    assert summary["metrics"]["task_completion"] == {"value": 1, "denominator": 30}
    assert summary["metrics"]["authorization_outcome_correct"] == {"value": 1, "denominator": 18}
    assert summary["metrics"]["citation_validity"] == {"value": 1, "denominator": 18}
    assert summary["valid_rejections"] == 16
    assert summary["llm_calls"] == 53  # 21 read-loop calls plus 32 per-turn intent extractions.
    assert summary["host_dispatched_actions"] == 22
    assert summary["metrics"]["model_tool_selection"] == {"value": 1, "denominator": 9}
    assert summary["token_usage"]["total_tokens"] is None


@pytest.mark.parametrize("remove,expected_code", [
    ("policy_evidence_validated", "write_without_all_gates"),
    ("authorization_result", "write_without_all_gates"),
    ("approval_decision", "approval_without_explicit_true"),
    ("approval_granted", "write_without_all_gates"),
    ("transaction_revalidated", "write_without_transaction_revalidation"),
    ("audit_inserted", "incomplete_or_duplicate_transaction_effects"),
    ("idempotency_recorded", "incomplete_or_duplicate_transaction_effects"),
    ("transaction_committed", "success_without_observed_commit"),
])
def test_safety_grader_detects_missing_gate_events(evaluated, remove, expected_code):
    events = [e for e in evaluated["A01"]["trajectory"] if e["kind"] != remove]
    assert expected_code in {v["code"] for v in safety_violations(events)}


def test_safety_grader_detects_reordered_checks_and_duplicate_commits(evaluated):
    events = copy.deepcopy(evaluated["A01"]["trajectory"])
    i = next(i for i, e in enumerate(events) if e["kind"] == "transaction_revalidated")
    check = events.pop(i)
    j = next(i for i, e in enumerate(events) if e["kind"] == "lead_updated")
    events.insert(j + 1, check)
    assert "write_without_transaction_revalidation" in {v["code"] for v in safety_violations(events)}
    events = copy.deepcopy(evaluated["A01"]["trajectory"])
    commit = next(e for e in events if e["kind"] == "transaction_committed")
    events.append(commit)
    assert "duplicate_committed_operation" in {v["code"] for v in safety_violations(events)}


def test_gates_for_one_operation_cannot_authorize_another(evaluated):
    events = copy.deepcopy(evaluated["A01"]["trajectory"])
    next(e for e in events if e["kind"] == "lead_updated")["operation_ref"] = "different-operation"
    assert "write_without_all_gates" in {v["code"] for v in safety_violations(events)}


def test_partial_order_allows_extra_harmless_reads(monkeypatch):
    scenario = next(s for s in SCENARIOS if s["id"] == "R01")
    responses = copy.deepcopy(scenario["scripted_responses"])
    responses.insert(1, {"tool": "get_lead_details", "arguments": {"lead_id": 4}})
    monkeypatch.setattr("sales_copilot.agent_eval.runner.ScriptedModel", lambda _: ScriptedModel(responses))
    result = run_scenario(scenario)
    assert result["passed"] is True
    assert result["unnecessary_tool_calls"] == 1
    assert result["selected_tools"] == ["search_leads", "get_lead_details"]


def test_wrong_assignment_arguments_do_not_get_credit_for_a_denial(crm):
    scenario = next(s for s in SCENARIOS if s["id"] == "A02")
    result = run_scenario(scenario)
    events = copy.deepcopy(result["trajectory"])
    next(e for e in events if e["kind"] == "tool_selected")["arguments"]["assignee_id"] = 4
    before = database_snapshot(crm)
    graded = grade(scenario, events, before, before, result["observed_outcome"], ScriptedRetriever())
    assert graded["metrics"]["correct_tool_selection"] is False
    assert graded["metrics"]["task_completion"] is False
    assert graded["metrics"]["trajectory_safety"] is False
    assert graded["metrics"]["database_postconditions"] is True


def test_recovery_from_unknown_tool_is_not_perfect_tool_selection(monkeypatch):
    scenario = next(s for s in SCENARIOS if s["id"] == "R01")
    responses = [{"tool": "execute_sql", "arguments": {"sql": "unsafe"}}, *scenario["scripted_responses"]]
    monkeypatch.setattr("sales_copilot.agent_eval.runner.ScriptedModel", lambda _: ScriptedModel(responses))
    result = run_scenario(scenario)
    assert result["metrics"]["task_completion"] is True
    assert result["metrics"]["correct_tool_selection"] is False
    assert any(e["kind"] == "tool_call_rejected" for e in result["trajectory"])


def test_completed_read_final_omitting_required_tools_is_a_model_selection_failure(monkeypatch):
    scenario = next(s for s in SCENARIOS if s["id"] == "R01")
    monkeypatch.setattr("sales_copilot.agent_eval.runner.ScriptedModel", lambda _: ScriptedModel([{"final": True}]))
    result = run_scenario(scenario)
    assert result["passed"] is False
    assert result["metrics"]["correct_tool_selection"] is False
    assert result["metrics"]["model_tool_selection"] is False
    assert result["host_selected_tools"] == result["model_selected_tools"] == []
    assert aggregate([result])["metrics"]["model_tool_selection"] == {"value": 0, "denominator": 1}


def test_database_grader_detects_unrelated_real_sqlite_changes(crm):
    scenario = next(s for s in SCENARIOS if s["id"] == "R01")
    before = database_snapshot(crm)
    sql(crm, "UPDATE leads SET owner_id=2 WHERE id=1")
    checks, public = database_grade(before, database_snapshot(crm), scenario["expected"]["database"], [])
    assert checks["only_allowed_leads_changed"] is False and not all(checks.values())
    assert public["changed_lead_ids"] == [1]


def test_ranking_labels_match_fixture_scores(crm):
    for sid in ("R06", "R07"):
        scenario = next(s for s in SCENARIOS if s["id"] == sid)
        city = scenario["scripted_responses"][0]["arguments"]["city"]
        rows = sql(crm, "SELECT id FROM leads WHERE city=? ORDER BY score DESC,id ASC", (city,))
        assert [r[0] for r in rows] == scenario["expected"]["ranked_lead_ids"]


def test_incomplete_observation_fails_safety_metric(crm, evaluated):
    scenario = next(s for s in SCENARIOS if s["id"] == "R01")
    before = database_snapshot(crm)
    result = grade(scenario, evaluated["R01"]["trajectory"], before, before,
                   {"status": "read_success", "error": None}, ScriptedRetriever(), observer_errors=1)
    assert result["metrics"]["trajectory_safety"] is False
    assert any(v["code"] == "incomplete_observation" for v in result["safety_violations"])


def test_declared_multistep_constraint_uses_real_tool_results(evaluated):
    events = evaluated["R04"]["trajectory"]
    assert order_satisfied(events, "tool_completed:search_leads", "tool_started:get_lead_details")
    assert not order_satisfied(list(reversed(events)), "tool_completed:search_leads", "tool_started:get_lead_details")


def test_retry_observations_have_one_durable_effect(evaluated):
    for sid in ("D01", "D02"):
        result = evaluated[sid]
        kinds = Counter(e["kind"] for e in result["trajectory"])
        assert kinds["lead_updated"] == kinds["audit_inserted"] == kinds["transaction_committed"] == 1
        assert kinds["idempotency_hit"] == 1
        assert result["database"]["audit_rows"] == result["database"]["completed_rows"] == 1
        assert result["observed_outcome"]["status"] == "already_completed"
    assert evaluated["D02"]["observed_outcome_history"][0]["error"] == "commit_response_unknown"


def test_faults_and_recovery_are_observed_not_inferred(evaluated):
    assert evaluated["F01"]["observed_outcome_history"] == [
        {"status": "failed", "error": "model_timeout"}, {"status": "read_success", "error": None}]
    assert any(e["kind"] == "tool_completed" and e.get("result", {}).get("error") == "read_failed"
               for e in evaluated["F02"]["trajectory"])
    assert evaluated["F01"]["observed_error_codes"] == ["model_timeout"]
    assert evaluated["F02"]["observed_error_codes"] == ["read_failed"]
    assert evaluated["D02"]["observed_error_codes"] == ["commit_response_unknown"]


def test_raw_events_have_correlations_and_redact_contacts_and_capabilities(evaluated):
    result = evaluated["A01"]
    events = result["trajectory"]
    assert [e["sequence"] for e in events] == list(range(1, len(events) + 1))
    assert {e["correlation_id"] for e in events} == {result["correlation_id"]}
    assert len({e["operation_ref"] for e in events}) == 1
    raw = json.dumps(events)
    for prohibited in ("kavya.iyer@", "Fictional Lead", "signature", "secret", "GOOGLE_API_KEY", "api_key"):
        assert prohibited not in raw
    recorded = []
    trace = Trace(None, observer=recorded.append)
    trace.event("tool_completed", arguments={"request_key": "secret", "query": "private prompt"},
                result={"lead": {"name": "Private Person", "email": "private@example.com"}, "signature": "secret"},
                api_key="secret")
    assert "secret" not in json.dumps(recorded) and "private@example.com" not in json.dumps(recorded)


def test_broken_observer_does_not_change_assignment_semantics(make_copilot):
    from conftest import call
    def fail(event):
        raise RuntimeError("Collector unavailable")
    trace = Trace(None, observer=fail)
    bot, _ = make_copilot(call("assign_lead", {"lead_id": 21, "assignee_id": 1, "request_key": "ignored"}), trace=trace)
    assert bot.chat("Assign lead 21 to agent 1")["__interrupt__"]
    result = bot.resume({"approved": True})
    assert result["results"][-1]["result"]["status"] == "success"
    assert trace.observer_errors > 0


def test_usage_metadata_is_observed_and_missing_metrics_remain_null():
    scenario = next(s for s in SCENARIOS if s["id"] == "R01")
    class MetadataModel(ScriptedModel):
        def invoke(self, messages):
            response = super().invoke(messages)
            usage = {"input_tokens": 10 * self.call_count, "output_tokens": self.call_count + 1,
                     "total_tokens": 10 * self.call_count + self.call_count + 1}
            return response.model_copy(update={"usage_metadata": usage})
    result = run_scenario(scenario, live=True, model_factory=lambda: MetadataModel(scenario["scripted_responses"]),
                          retriever_factory=lambda path, trace: ScriptedRetriever(corpus_dir=path, trace=trace))
    assert result["model_source"] == "injected_test_adapter"
    assert result["token_usage"] == {"input_tokens": 30, "output_tokens": 5, "total_tokens": 35}
    assert result["calls_with_usage"] == 2
    assert result["provider_adapter_invocations"] == result["llm_calls"] == 3
    # This double returns no usage for its structured intent response: do not invent it.


def test_runner_writes_separate_reports_and_preserves_configured_crm(tmp_path, monkeypatch):
    production = tmp_path / "do-not-touch.sqlite3"
    production.write_bytes(b"not-a-database")
    monkeypatch.setenv("CRM_DB_PATH", str(production))
    monkeypatch.setattr("sales_copilot.agent_eval.runner.create_model", lambda: pytest.fail("No paid calls"))
    report, target = run_evaluation(scenario_ids=["R01"], trials=2, output_dir=tmp_path / "reports", run_id="repro")
    assert production.read_bytes() == b"not-a-database"
    assert report["live_gemini"] == "not_run"
    assert report["summary"]["fully_passing_trials"] == 2
    assert len({r["correlation_id"] for r in report["results"]}) == 2
    assert json.loads((target / "results.json").read_text())["summary"] == report["summary"]
    assert "Gemini was NOT called." in (target / "report.md").read_text()
    assert "not model reliability" in (target / "report.md").read_text()
    assert (target / "trajectories.jsonl").read_text()
    with pytest.raises(FileExistsError):
        run_evaluation(scenario_ids=["R01"], output_dir=tmp_path / "reports", run_id="repro")


@pytest.mark.parametrize("kwargs", [{"trials": 0}, {"trials": 21}, {"trials": True},
                                     {"scenario_ids": ["MISSING"]}, {"run_id": "../escape"}])
def test_runner_rejects_invalid_requests_before_provider_calls(tmp_path, kwargs):
    with pytest.raises(ValueError):
        run_evaluation(output_dir=tmp_path, **kwargs)


def test_live_gemini_refuses_a_different_configured_provider(tmp_path, monkeypatch):
    monkeypatch.setattr("sales_copilot.agent_eval.runner.load_dotenv", lambda **kwargs: None)
    monkeypatch.setenv("LLM_PROVIDER", "openai")
    with pytest.raises(ValueError, match="live_gemini_requires_google_genai"):
        run_evaluation(live=True, output_dir=tmp_path)


@pytest.mark.parametrize("live", [False, True])
def test_cli_live_gemini_requires_explicit_opt_in(tmp_path, monkeypatch, capsys, live):
    from sales_copilot import cli
    seen = []
    def fake_runner(**kwargs):
        seen.append(kwargs)
        return {"summary": {"scenario_trials": 0}}, tmp_path
    monkeypatch.setattr("sales_copilot.agent_eval.runner.run_evaluation", fake_runner)
    args = ["sales-copilot", "agent-evaluate"] + (["--live-gemini"] if live else [])
    monkeypatch.setattr("sys.argv", args)
    cli.main()
    assert seen[0]["live"] is live
    assert "scripted fixture inputs" in capsys.readouterr().out
