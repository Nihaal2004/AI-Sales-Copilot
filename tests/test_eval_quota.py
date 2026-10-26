"""No provider calls, downloads or real sleeps in these regression tests."""

import copy
import hashlib
import json
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from google.genai.errors import ClientError, ServerError

from sales_copilot.agent_eval.availability import availability_summary, trial_availability
from sales_copilot.agent_eval.fixtures import ScriptedModel, ScriptedRetriever
from sales_copilot.agent_eval.quota import QuotaConfig, QuotaController, provider_diagnostic
from sales_copilot.agent_eval.runner import load_scenarios, run_evaluation, run_scenario
from sales_copilot.tracing import Trace


def error(status=429, quota=None, retry=None, headers=None):
    details = []
    if quota:
        details.append({"@type": "type.googleapis.com/google.rpc.QuotaFailure",
                        "violations": [{"quotaId": q, "quotaMetric": "generate_content_requests",
                                        "description": "secret private@example.com"} for q in quota]})
    if retry is not None:
        details.append({"@type": "type.googleapis.com/google.rpc.RetryInfo", "retryDelay": retry})
    cls = ClientError if status < 500 else ServerError
    return cls(status, {"error": {"code": status, "message": "secret private@example.com", "details": details}},
               SimpleNamespace(headers=headers or {}, status_code=status))


class Clock:
    def __init__(self):
        self.now, self.sleeps = 0.0, []

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.now += seconds

    def controller(self, **kwargs):
        return QuotaController(QuotaConfig(**kwargs), sleep=self.sleep, clock=lambda: self.now,
                               jitter=lambda low, high: high)


@pytest.mark.parametrize("identifier,expected,daily", [
    ("GenerateRequestsPerMinutePerProjectPerModel-FreeTier", "RPM", False),
    ("GenerateContentInputTokensPerMinutePerModel", "TPM", False),
    ("GenerateRequestsPerDayPerProjectPerModel", "RPD", True),
    ("GenerateContentTokensPerDay", "unknown", True),
    ("UnspecifiedQuota", "unknown", False),
])
def test_structured_quota_classification(identifier, expected, daily):
    value = provider_diagnostic(error(quota=[identifier]))
    assert value["quota_types"] == [expected]
    assert value["daily_exhausted"] is daily
    assert "secret" not in json.dumps(value)


def test_unknown_does_not_guess_from_exception_prose_and_wrapped_details_survive():
    original = error(quota=["GenerateRequestsPerDay"])
    wrapper = RuntimeError("RPM TPM RPD secret")
    wrapper.__cause__ = original
    assert provider_diagnostic(wrapper)["quota_types"] == ["RPD"]
    assert provider_diagnostic(error())["quota_types"] == ["unknown"]
    assert provider_diagnostic(RuntimeError("429 daily quota exhausted"))["availability"] == "model_error"


def test_retry_after_seconds_date_and_retryinfo_take_largest_safe_delay():
    assert provider_diagnostic(error(headers={"Retry-After": "5"}, retry="7.5s"))["retry_after_seconds"] == 7.5
    now = datetime(2026, 10, 25, tzinfo=timezone.utc)
    assert provider_diagnostic(error(headers={"retry-after": "Sun, 25 Oct 2026 00:00:09 GMT"}), now=now)["retry_after_seconds"] == 9
    assert provider_diagnostic(error(retry={"seconds": "2", "nanos": 500000000}))["retry_after_seconds"] == 2.5
    assert provider_diagnostic(error(headers={"retry-after": "private@example.com"}, retry="NaNs"))["retry_after_seconds"] is None


@pytest.mark.parametrize("status", [429, 503])
def test_transient_retries_are_bounded_paced_and_redacted(status):
    clock, events = Clock(), []
    quota = clock.controller(call_interval=4, max_attempts=3)
    delegate = Mock()
    delegate.invoke.side_effect = [error(status, retry="6s"), error(status), "verified_response"]
    trace = Trace(None, observer=events.append)
    assert quota.invoke(delegate, [], trace) == "verified_response"
    assert delegate.invoke.call_count == 3
    assert clock.sleeps == [6, 4]
    assert [e["attempt"] for e in events if e["kind"] == "provider_invocation"] == [1, 2, 3]
    assert len([e for e in events if e["kind"] == "provider_retry_scheduled"]) == 2
    assert "secret" not in json.dumps(events) and "private@example.com" not in json.dumps(events)


def test_daily_quota_stops_without_retry_even_with_retryinfo():
    clock = Clock()
    quota = clock.controller()
    delegate = Mock()
    delegate.invoke.side_effect = error(quota=["GenerateRequestsPerMinute", "GenerateRequestsPerDay"], retry="2s")
    with pytest.raises(ClientError):
        quota.invoke(delegate, [], Trace(None))
    assert delegate.invoke.call_count == 1 and clock.sleeps == []
    assert quota.halt_reason == "daily_quota_exhausted"
    with pytest.raises(RuntimeError):
        quota.invoke(delegate, [], Trace(None))
    assert delegate.invoke.call_count == 1


@pytest.mark.parametrize("status", [400, 401, 403, 404, 500, 504])
def test_nontransient_errors_not_retried(status):
    clock = Clock()
    delegate = Mock()
    delegate.invoke.side_effect = error(status)
    with pytest.raises(Exception):
        clock.controller().invoke(delegate, [], Trace(None))
    assert delegate.invoke.call_count == 1 and clock.sleeps == []


def test_retry_after_beyond_budget_is_deferred_not_clamped_or_ignored():
    clock, events = Clock(), []
    quota = clock.controller(max_retry_wait=10)
    delegate = Mock()
    delegate.invoke.side_effect = error(retry="3600s")
    with pytest.raises(ClientError):
        quota.invoke(delegate, [], Trace(None, observer=events.append))
    assert delegate.invoke.call_count == 1 and clock.sleeps == []
    assert quota.halt_reason == "retry_after_exceeds_wait_budget"
    assert events[-1]["kind"] == "provider_retry_deferred"


def test_exponential_backoff_jitter_cap_and_attempt_budget():
    clock, events = Clock(), []
    quota = clock.controller(call_interval=0, max_attempts=5, backoff_base=2, backoff_cap=5)
    delegate = Mock()
    delegate.invoke.side_effect = error()
    with pytest.raises(ClientError):
        quota.invoke(delegate, [], Trace(None, observer=events.append))
    assert clock.sleeps == [2, 4, 5, 5]
    assert delegate.invoke.call_count == 5
    assert quota.halt_reason == "quota_retry_budget_exhausted"


@pytest.mark.parametrize("kwargs", [{"call_interval": -1}, {"batch_pause": float("inf")},
                                    {"backoff_base": float("nan")}, {"max_attempts": 0},
                                    {"max_attempts": 11}, {"max_attempts": True}, {"batch_size": 0}])
def test_invalid_config_fails_locally(kwargs):
    with pytest.raises(ValueError):
        QuotaConfig(**kwargs)


def scenario(sid):
    return next(s for s in load_scenarios() if s["id"] == sid)


def canonical(path, trace):
    return ScriptedRetriever(corpus_dir=path, trace=trace)


class FailingModel(ScriptedModel):
    def __init__(self, responses, failure):
        super().__init__(responses)
        self.failure = failure

    def invoke(self, messages):
        if self.failure is not None:
            failure, self.failure = self.failure, None
            raise failure
        return super().invoke(messages)


def test_d01_first_turn_failure_does_not_issue_false_duplicate_probe():
    from sales_copilot.agent_eval.fixtures import ScriptedIntentResponse
    class IntentFailureModel(ScriptedModel):
        def with_structured_output(self, schema, **kwargs):
            return ScriptedIntentResponse(schema, error())
    s = scenario("D01")
    result = run_scenario(s, live=True, model_factory=lambda: IntentFailureModel(s["scripted_responses"]),
                          retriever_factory=canonical, quota=Clock().controller(max_attempts=1))
    assert result["passed"] is False  # The original already_completed contract remains strict.
    assert result["availability"]["status"] == "model_rate_limited"
    assert len(result["observed_outcome_history"]) == 1
    assert result["database"]["audit_rows"] == result["database"]["completed_rows"] == 0
    assert any(e["kind"] == "evaluation_prerequisite_unmet" for e in result["trajectory"])
    assert not any(e["kind"] == "lead_updated" for e in result["trajectory"])


def test_successful_d01_still_requires_real_idempotency_hit():
    s = scenario("D01")
    result = run_scenario(s)
    assert result["passed"] is True and result["observed_outcome"]["status"] == "already_completed"
    assert sum(e["kind"] == "transaction_committed" for e in result["trajectory"]) == 1
    assert sum(e["kind"] == "idempotency_hit" for e in result["trajectory"]) == 1
    # If a clean duplicate returns success rather than already_completed it is
    # a genuine contract failure, never reclassified as rate limiting.
    broken = copy.deepcopy(result)
    broken["passed"] = False
    assert trial_availability(broken)["agent_contract_failed"] is True
    assert trial_availability(broken)["model_rate_limited"] is False


def test_recovered_429_is_observed_but_not_counted_as_clean_conditional_trial():
    s = scenario("R01")
    result = run_scenario(s, live=True, model_factory=lambda: FailingModel(s["scripted_responses"], error()),
                          retriever_factory=canonical, quota=Clock().controller())
    assert result["passed"] is True and result["llm_failed_calls"] == 0
    assert result["provider_adapter_invocations"] == result["llm_calls"] + 1
    summary = availability_summary([result])
    assert summary["overall_pass_rate"] == 1
    assert summary["trial_counts"]["model_available"] == summary["trial_counts"]["model_rate_limited"] == 1
    assert summary["conditional_agent_success"]["value"] is None
    assert summary["conditional_agent_success"]["denominator"] == 0
    assert result["availability"]["resume_incomplete"] is False


def test_injected_timeout_is_not_gemini_failure():
    result = run_scenario(scenario("F01"))
    value = trial_availability(result)
    assert value["model_available"] and value["conditional_eligible"]
    assert not value["model_error"] and not value["model_rate_limited"]


def test_interrupted_resume_preserves_parent_completed_trials_and_skips_them(tmp_path, monkeypatch):
    from sales_copilot.agent_eval import runner
    original = runner.run_scenario
    calls = []
    def interrupt(s, **kwargs):
        calls.append(s["id"])
        if s["id"] == "R02":
            raise KeyboardInterrupt
        return original(s, **kwargs)
    monkeypatch.setattr(runner, "run_scenario", interrupt)
    first, parent = run_evaluation(scenario_ids=["R01", "R02"], output_dir=tmp_path, run_id="parent")
    assert first["execution_status"] == "paused"
    hashes = {p: hashlib.sha256(p.read_bytes()).hexdigest() for p in parent.rglob("*") if p.is_file()}
    calls.clear()
    monkeypatch.setattr(runner, "run_scenario", lambda s, **kw: (calls.append(s["id"]), original(s, **kw))[1])
    child, target = run_evaluation(resume_from=parent, output_dir=tmp_path, run_id="child")
    assert calls == ["R02"]
    assert child["summary"]["fully_passing_trials"] == 2
    assert child["pending_trials"] == []
    assert child["results"][0]["correlation_id"] == first["results"][0]["correlation_id"]
    assert all(hashlib.sha256(p.read_bytes()).hexdigest() == digest for p, digest in hashes.items())
    assert (target / "trials" / "R02-1.json").exists()
    assert not (target / "trials" / "R01-1.json").exists()


def test_resume_after_process_kill_reads_immutable_trials_without_final_report(tmp_path):
    from sales_copilot.agent_eval.artifacts import read_parent
    report, target = run_evaluation(scenario_ids=["R01"], output_dir=tmp_path, run_id="killed")
    (target / "results.json").unlink()  # Simulate a process killed before final report.
    (target / "trials" / "R02-1.json.pending").write_text("{incomplete")
    header, results = read_parent(target)
    assert header["execution_status"] == "running" and results == report["results"]


def test_runner_shared_call_and_batch_pacing_without_network(tmp_path, monkeypatch):
    from sales_copilot.agent_eval import runner
    monkeypatch.setattr(runner, "PolicyRetriever", ScriptedRetriever)
    clock = Clock()
    s = scenario("R01")  # Same read/final script works for both identical R01 trials.
    report, target = run_evaluation(live=True, scenario_ids=["R01"], trials=2, output_dir=tmp_path,
                                    model_factory=lambda: ScriptedModel(s["scripted_responses"]),
                                    quota_controller=clock.controller(call_interval=4, batch_size=1, batch_pause=30))
    assert report["summary"]["fully_passing_trials"] == 2
    assert clock.sleeps == [4, 4, 30, 4, 4]  # Intent normalization is also paced.
    assert "scenario_batch" in (target / "runner_events.jsonl").read_text()
    assert report["manifest"]["sdk_total_attempts"] == 1


def test_daily_stop_saves_result_and_resumes_only_incomplete_trials(tmp_path, monkeypatch):
    from sales_copilot.agent_eval import runner
    monkeypatch.setattr(runner, "PolicyRetriever", ScriptedRetriever)
    s = scenario("R01")
    factory = lambda: FailingModel(s["scripted_responses"], error(quota=["GenerateRequestsPerDay"]))
    first, parent = run_evaluation(live=True, scenario_ids=["R01", "R02"], output_dir=tmp_path,
                                   model_factory=factory, quota_controller=Clock().controller())
    assert first["stop_reason"] == "daily_quota_exhausted"
    assert len(first["results"]) == 1 and len(first["pending_trials"]) == 2
    # Preserve the failure in parent; resume whole trials against fresh fixture DBs.
    saved = (parent / "results.json").read_bytes()
    scripts = iter([scenario("R01")["scripted_responses"], scenario("R02")["scripted_responses"]])
    second, _ = run_evaluation(live=True, resume_from=parent, output_dir=tmp_path,
                               model_factory=lambda: ScriptedModel(next(scripts)), quota_controller=Clock().controller())
    assert second["summary"]["fully_passing_trials"] == 2
    assert (parent / "results.json").read_bytes() == saved
    assert second["resume_parent"]["run_id"] == first["run_id"]


def test_actual_installed_google_sdk_is_single_attempt_without_any_api_call(monkeypatch):
    from langchain_core.messages import HumanMessage
    from langchain_google_genai import ChatGoogleGenerativeAI
    from google.genai._api_client import retry_args
    monkeypatch.setattr("langchain_google_genai.chat_models.Client", Mock())
    model = ChatGoogleGenerativeAI(model="gemini-3.5-flash-lite", api_key="unit-test-placeholder",
                                   vertexai=False, max_retries=1)
    request = model._prepare_request([HumanMessage(content="test")])
    options = request["config"].http_options.retry_options
    assert options.attempts == 1
    stop = retry_args(options)["stop"]
    assert stop(SimpleNamespace(attempt_number=1)) is True


def test_resume_keeps_genuine_completed_contract_failures():
    from sales_copilot.agent_eval.artifacts import resumable_results
    result = run_scenario(scenario("R01"))
    result["passed"] = False
    result["failures"] = ["correct_tool_selection"]
    assert resumable_results([result]) == [result]
    assert trial_availability(result)["status"] == "agent_contract_failed"
    assert availability_summary([result])["conditional_agent_success"] == {
        "value": 0, "numerator": 0, "denominator": 1}
    result["execution_exception"] = "RuntimeError"
    availability = trial_availability(result)
    assert availability["status"] == "agent_contract_failed"
    assert availability["conditional_eligible"] and not availability["model_error"]
    assert availability["resume_incomplete"] is False


def test_saved_live_run_diagnosis_and_resume_selection_are_read_only():
    from pathlib import Path
    from sales_copilot.agent_eval.artifacts import read_parent, resumable_results
    folder = Path(__file__).parents[1] / "docs/stage4_runs/719d4b8f-38f0-4be2-b8ca-6fd51f1bdb8b"
    saved = {p: p.read_bytes() for p in folder.iterdir() if p.is_file()}
    report, results = read_parent(folder)
    summary = availability_summary(results)
    assert report["summary"]["llm_failed_calls"] == 16
    assert summary["overall_pass_rate"] == .5
    assert summary["conditional_agent_success"] == {"value": 1, "numerator": 15, "denominator": 15}
    assert summary["trial_counts"] == {"model_available": 15, "model_rate_limited": 15,
                                       "model_error": 0, "agent_contract_failed": 0}
    assert len(resumable_results(results)) == 15
    duplicate = next(r for r in results if r["scenario_id"] == "D01")
    assert duplicate["observed_outcome_history"] == [{"status": "failed", "error": "model_rate_limited"},
                                                   {"status": "success", "error": None}]
    assert duplicate["database"]["audit_rows"] == duplicate["database"]["completed_rows"] == 1
    assert sum(e["kind"] == "transaction_committed" for e in duplicate["trajectory"]) == 1
    assert not any(e["kind"] == "idempotency_hit" for e in duplicate["trajectory"])
    assert all(p.read_bytes() == content for p, content in saved.items())


def test_cli_forwards_quota_configuration_and_resume_without_api_calls(tmp_path, monkeypatch):
    from sales_copilot import cli
    seen = []
    def runner(**kwargs):
        seen.append(kwargs)
        return {"summary": {}}, tmp_path
    monkeypatch.setattr("sales_copilot.agent_eval.runner.run_evaluation", runner)
    monkeypatch.setattr("sys.argv", ["sales-copilot", "agent-evaluate", "--resume-from", str(tmp_path),
                                    "--call-interval", "10", "--batch-size", "2", "--batch-pause", "60",
                                    "--max-attempts", "4", "--backoff-base", "3", "--backoff-cap", "20",
                                    "--max-retry-wait", "90"])
    cli.main()
    assert seen[0]["live"] is False
    assert seen[0]["resume_from"] == str(tmp_path)
    assert seen[0]["quota_config"] == QuotaConfig(10, 2, 60, 4, 3, 20, 90)
