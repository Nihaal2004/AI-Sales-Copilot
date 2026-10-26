"""Reproducible isolated graph evaluation, with explicitly opt-in live Gemini."""

import hashlib
import importlib.metadata
import json
import os
import platform
import re
import sqlite3
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from uuid import NAMESPACE_URL, uuid4, uuid5
from dotenv import load_dotenv

from ..assignment import AssignmentService
from ..corpus import DEFAULT_CORPUS, Corpus
from ..graph import Copilot
from ..model import create_model
from ..retrieval import EMBED_MODEL, RERANK_MODEL, PolicyRetriever
from ..tracing import Trace
from .fixtures import (LiveModelAdapter, RetrievalFaults, ScriptedModel, ScriptedRetriever,
                       database_snapshot, install_read_fault, prepare_fixture)
from .grading import grade
from .quota import QuotaConfig, QuotaController
from .availability import availability_summary, trial_availability
from .artifacts import read_parent, resumable_results, write_json

DATASET = Path(__file__).resolve().parents[2] / "evaluation" / "agent_scenarios.json"


def source_fingerprint():
    root = Path(__file__).parents[1]
    return hashlib.sha256("".join(p.relative_to(root).as_posix() + ":" + hashlib.sha256(p.read_bytes()).hexdigest()
                                  for p in sorted(root.rglob("*.py"))).encode()).hexdigest()


def load_scenarios(path=DATASET):
    dataset = json.loads(Path(path).read_text(encoding="utf-8"))
    scenarios = dataset["scenarios"]
    if dataset["version"] != 1 or not 25 <= len(scenarios) <= 30:
        raise ValueError("invalid_scenario_dataset")
    ids = [s["id"] for s in scenarios]
    if len(set(ids)) != len(ids) or any(not re.fullmatch(r"[A-Z][0-9]{2}", i) for i in ids):
        raise ValueError("invalid_scenario_ids")
    for s in scenarios:
        if type(s["approval"]["approved"]) is not bool or not s["expected"]["required_events"]:
            raise ValueError("invalid_scenario_contract")
        if any(len(pair) != 2 for pair in s["expected"]["ordering"]):
            raise ValueError("invalid_order_contract")
    return scenarios


def observed_outcome(result):
    items = result.get("results", [])
    incomplete = [i["result"] for i in items if i["tool"] == "completion"]
    if incomplete:
        return {"status": incomplete[-1]["status"], "error": incomplete[-1]["error"]}
    assignments = [i["result"] for i in items if i["tool"] == "assign_lead"]
    if assignments:
        last = assignments[-1]
        return {"status": last.get("status", "operation_failed"), "error": last.get("error")}
    if any(i["result"].get("ok") is True for i in items):
        return {"status": "read_success", "error": None}
    last = items[-1]["result"] if items else {}
    return {"status": "failed", "error": last.get("error", "no_verified_result")}


def run_scenario(scenario, *, trial=1, run_id="scripted", live=False, real_retrieval=False, model_factory=None,
                 retriever_factory=None, quota=None):
    """Never accepts a CRM path. Every trial creates an exclusive disposable DB."""
    events = []
    correlation_id = f"{run_id}:{scenario['id']}:{trial}"
    trace = Trace(None, observer=events.append, correlation_id=correlation_id)
    operation_id = str(uuid5(NAMESPACE_URL, correlation_id + ":operation"))
    trace.operation(operation_id)
    result, execution_exception, outcomes = {}, None, []
    started = time.perf_counter()
    with tempfile.TemporaryDirectory(prefix="copilot-agent-eval-") as folder:
        crm, actor, corpus_dir = prepare_fixture(folder, scenario["fixture"])
        before = database_snapshot(crm)
        # Canonical-only oracle exists even if loading a real model fails; it is
        # never a fallback execution retriever or a fabricated successful result.
        retriever = ScriptedRetriever(corpus_dir=corpus_dir, trace=trace)
        try:
            retriever = (retriever_factory(corpus_dir, trace) if retriever_factory else
                         (PolicyRetriever if live or real_retrieval else ScriptedRetriever)(corpus_dir=corpus_dir, trace=trace))
            retriever = RetrievalFaults(retriever, scenario["fixture"].get("policy_mode", "normal"))
            if scenario["fault"] == "read_error_once":
                install_read_fault(crm)
            def fault(point):
                if scenario["fault"] == point:
                    trace.event("injected_backend_failure", fault=point)
                    raise TimeoutError("Controlled evaluation failure")
            service = AssignmentService(crm, retriever, actor_id=actor, trace=trace, fault=fault)
            if live:
                model = LiveModelAdapter((model_factory or create_model)(), trace,
                                         quota=quota,
                                         inject_timeout=scenario["fault"] == "model_timeout_once")
            else:
                model = ScriptedModel(scenario["scripted_responses"])
            bot = Copilot(crm, model, thread_id=correlation_id, trace=trace, policy_retriever=retriever,
                          actor_id=actor, assignment_service=service)
            trace.event("evaluation_started", mode="live" if live else "scripted", trial=trial,
                        approval_source="scripted_fixture_decision", database="isolated_temporary_fixture")
            def finish_turn(current):
                if current.get("__interrupt__"):
                    payload = current["__interrupt__"][0].value
                    # Approve only the exact declared fictional action. Never let an
                    # unexpected model proposal inherit a different action's decision.
                    declared = [r for r in scenario["scripted_responses"] if r.get("tool") == "assign_lead"]
                    action = payload["action"]
                    exact = any(action["lead_id"] == r["arguments"]["lead_id"]
                                and action["assignee_id"] == r["arguments"]["assignee_id"] for r in declared)
                    decision = scenario["approval"]["approved"] if exact else False
                    trace.event("evaluation_decision", approved=decision, expected_action=exact,
                                source="explicit_scripted_fixture_only")
                    current = bot.resume({"approved": decision})
                outcomes.append(observed_outcome(current))
                return current
            result = finish_turn(bot.chat(scenario["request"], operation_id=operation_id))
            followup = scenario["followup"]
            # A duplicate probe needs a verified completed first operation. A
            # first-turn outage must not turn it into a first assignment probe.
            if followup and followup["mode"] == "duplicate" and not (
                    outcomes[-1]["status"] == "success" and any(e["kind"] == "transaction_committed" for e in events)):
                trace.event("evaluation_prerequisite_unmet", prerequisite="duplicate_requires_initial_commit")
            elif followup:
                if followup["mode"] == "retry":
                    result = bot.retry_last()
                    outcomes.append(observed_outcome(result))
                else:
                    key = operation_id if followup["mode"] == "duplicate" else str(uuid5(NAMESPACE_URL, correlation_id + ":recovery"))
                    result = finish_turn(bot.chat(followup["request"], operation_id=key))
        except Exception as exc:
            # Never save raw exception strings, provider payloads, prompts or credentials.
            execution_exception = type(exc).__name__
            trace.event("evaluation_failed", error_type=execution_exception)
        after = database_snapshot(crm)
        outcome = observed_outcome(result)
        graded = grade(scenario, events, before, after, outcome, retriever,
                       observer_errors=trace.observer_errors, exception=execution_exception)
        record = {"scenario_id": scenario["id"], "trial": trial, "correlation_id": correlation_id,
                "category": scenario["category"], "mode": "live" if live else "scripted",
                "model_source": "injected_test_adapter" if live and model_factory else "real_gemini" if live else "scripted",
                "retrieval": "real_hybrid" if live or real_retrieval else "scripted_canonical_evidence",
                "approval_source": "explicit_scripted_fixture_decisions", "request": scenario["request"],
                "fault": scenario["fault"],
                "initial_fixture": scenario["fixture"], "expected": scenario["expected"], **graded,
                "observed_outcome_history": outcomes, "execution_exception": execution_exception,
                "observer_errors": trace.observer_errors, "latency_ms": round((time.perf_counter() - started) * 1000, 3),
                "trajectory": events}
        record["availability"] = trial_availability(record)
        return record


def aggregate(results):
    metric_keys = ("task_completion", "correct_tool_selection", "model_tool_selection", "required_step_coverage", "trajectory_safety",
                   "authorization_outcome_correct", "citation_validity", "database_postconditions")
    metrics = {}
    for key in metric_keys:
        values = [r["metrics"][key] for r in results if r["metrics"].get(key) is not None]
        metrics[key] = {"value": sum(values) / len(values) if values else None, "denominator": len(values)}
    return {"scenario_trials": len(results), "fully_passing_trials": sum(r["passed"] for r in results),
            "availability": availability_summary(results),
            "metrics": metrics, "valid_rejections": sum(r["valid_rejection"] for r in results),
            "unnecessary_tool_calls": sum(r["unnecessary_tool_calls"] for r in results),
            "host_dispatched_actions": sum(len(r.get("host_selected_tools", [])) for r in results),
            "model_selected_tools": sum(len(r.get("model_selected_tools", [])) for r in results),
            "llm_calls": sum(r["llm_calls"] for r in results),
            "llm_failed_calls": sum(r["llm_failed_calls"] for r in results),
            "provider_adapter_invocations": sum(r["provider_adapter_invocations"] for r in results),
            "calls_with_usage": sum(r["calls_with_usage"] for r in results),
            "citations_valid": sum(r["citations_valid"] for r in results),
            "citations_observed": sum(r["citations_observed"] for r in results),
            "token_usage": {k: (sum(values) if (values := [r["token_usage"][k] for r in results if r["token_usage"][k] is not None]) else None)
                            for k in ("input_tokens", "output_tokens", "total_tokens")},
            "total_latency_ms": round(sum(r["latency_ms"] for r in results), 3)}


def markdown_report(report):
    mode = report["mode"]
    summary = report["summary"]
    lines = [f"# Stage 4 agent evaluation: {mode}", "", f"Run `{report['run_id']}`. "
             f"{summary['fully_passing_trials']}/{summary['scenario_trials']} scenario-trials satisfy every contract.", "",
             "All CRM mutations used isolated fictional databases and explicitly scripted fixture decisions. "
             + ("Gemini was NOT called. Scripted results measure application mechanics, not model reliability."
                if mode == "scripted" else "This opt-in run selected Gemini; consult provider invocation counts and failures. Outputs can vary across trials."), "",
             "| Metric | Observed value | Applicable scenario-trials |", "| --- | ---: | ---: |"]
    for name, item in summary["metrics"].items():
        value = "not observed" if item["value"] is None else f"{item['value']:.6f}"
        lines.append(f"| {name} | {value} | {item['denominator']} |")
    lines += ["", "`correct_tool_selection` measures application operation selection, including explicitly traced host dispatch. "
              "`model_tool_selection` applies to trials with an observed native-loop response or native selection/rejection, including omitted required tools; "
              "host-only assignments are excluded, not counted as LLM tool-calling successes. "
              "Historical events without provenance are not relabelled.",
              f"Host-dispatched actions: {summary.get('host_dispatched_actions', 0)}. "
              f"Explicitly model-selected tools: {summary.get('model_selected_tools', 0)}."]
    availability = summary["availability"]
    conditional = availability["conditional_agent_success"]
    lines += ["", "## Provider availability and conditional results", "",
              f"Availability flags (can overlap): `{json.dumps(availability['trial_counts'])}`.",
              f"Exclusive trial statuses: `{json.dumps(availability['status_counts'])}`.",
              f"Original overall all-contract pass rate: `{availability['overall_pass_rate']}`. "
              f"Conditional agent success: `{conditional['value']}` "
              f"({conditional['numerator']}/{conditional['denominator']}).",
              "Conditional results include completed trials with no observed rate limit and no unresolved "
              "provider failures. Expected injected faults are separate. Small/censored samples are not model reliability evidence.",
              f"Execution status: `{report.get('execution_status')}`; stop reason: `{report.get('stop_reason')}`; "
              f"incomplete trial count: {len(report.get('pending_trials', []))}.",
              "Resumed reports retain parent results and identify source differences; parent artifacts are never overwritten."]
    lines += ["", f"Valid expected rejections: {summary['valid_rejections']}. "
              f"Tool calls above declared minimum: {summary['unnecessary_tool_calls']}.",
              f"Graph LLM calls: {summary['llm_calls']}, failed calls: {summary['llm_failed_calls']}; "
              f"provider adapter invocations: {summary['provider_adapter_invocations']}. "
              f"Observed token usage: `{json.dumps(summary['token_usage'])}` from {summary['calls_with_usage']} calls. "
              "Null means unavailable, not zero. New live runs pin SDK attempts to one; application retry attempts are observed.",
              f"Total observed scenario latency: {summary['total_latency_ms']:.3f} ms (includes fixture setup; no human waiting).", "",
              "| Scenario | Trial | Outcome | All checks | Contract failures | Observed error codes (including expected rejections) |",
              "| --- | ---: | --- | --- | --- | --- |"]
    for r in report["results"]:
        outcome = r["observed_outcome"]
        lines.append(f"| {r['scenario_id']} | {r['trial']} | {outcome['status']} / {outcome.get('error') or '-'} | "
                     f"{'PASS' if r['passed'] else 'FAIL'} | {', '.join(r['failures']) or '-'} | "
                     f"{', '.join(r['observed_error_codes']) or '-'} |")
    lines += ["", "## Repeated-trial variability", "", "| Scenario | Passing trials | Task completions |", "| --- | ---: | ---: |"]
    for sid in dict.fromkeys(r["scenario_id"] for r in report["results"]):
        rows = [r for r in report["results"] if r["scenario_id"] == sid]
        lines.append(f"| {sid} | {sum(r['passed'] for r in rows)}/{len(rows)} | "
                     f"{sum(r['metrics']['task_completion'] for r in rows)}/{len(rows)} |")
    lines += ["", "## Failure details", ""]
    for r in report["results"]:
        if not r["passed"]:
            lines += [f"- **{r['scenario_id']}, trial {r['trial']}**: {r['request']}",
                      f"  Expected `{r['expected']['status']}`; observed `{json.dumps(r['observed_outcome'])}`. "
                      f"Failures: {', '.join(r['failures'])}. Correlation: `{r['correlation_id']}`."]
            if r["expected"]["ranked_lead_ids"]:
                final = next((e for e in reversed(r["trajectory"]) if e["kind"] == "final_result"), {})
                lines.append(f"  Expected displayed rank `{r['expected']['ranked_lead_ids']}`; "
                             f"observed `{final.get('displayed_lead_ids')}`. The current renderer preserves tool-result order.")
    lines += ["", "## Scope and interpretation", "",
              "Required steps use partial-order constraints, not an exact whole trajectory. Harmless extra reads are allowed "
              "and counted above the declared minimum. Safety checks are universal per operation/transaction. "
              "Accepted citations are checked against canonical chunks; rejected forged evidence is not counted as a valid citation. "
              "Missing citation/authorization opportunities have null metrics and are excluded from those denominators.", "",
              "Correct expected policy/permission/conflict/approval rejections count as task completions. Functional completion "
              "and safety are separate metrics. All-check pass additionally requires step coverage, ordering and absence of prohibited events. "
              "These are author-labelled development scenarios, not a general model-reliability benchmark. "
              "No full-text answer quality or externally authenticated permissions are evaluated. "
              "See docs/stage4_explained.md for definitions, fixture/fault semantics and opt-in live commands.", ""]
    return "\n".join(lines)


def run_evaluation(*, live=False, trials=1, scenario_ids=None, output_dir="docs/stage4_runs", run_id=None,
                   real_retrieval=False, dataset_path=DATASET, model_factory=None, quota_config=None,
                   resume_from=None, quota_controller=None):
    if type(trials) is not int or not 1 <= trials <= 20:
        raise ValueError("trials_must_be_1_to_20")
    if live and model_factory is None:
        load_dotenv(override=False)
        if os.getenv("LLM_PROVIDER", "google_genai") != "google_genai":
            raise ValueError("live_gemini_requires_google_genai_configuration")
    scenarios = load_scenarios(dataset_path)
    dataset_hash = hashlib.sha256(Path(dataset_path).read_bytes()).hexdigest()
    corpus_hash = Corpus().fingerprint
    parent, previous = (read_parent(resume_from) if resume_from else (None, []))
    if parent:
        if scenario_ids or trials != 1:
            raise ValueError("resume_inherits_original_scenario_selection_and_trials")
        if parent["mode"] != ("live" if live else "scripted") or parent["retrieval"] != (
                "real_hybrid" if live or real_retrieval else "scripted_canonical_evidence"):
            raise ValueError("resume_mode_mismatch")
        if (parent["manifest"]["dataset_sha256"] != dataset_hash or
                parent["manifest"]["corpus_fingerprint"] != corpus_hash or
                parent["manifest"]["fixture_version"] != "stage3_demo_v1"):
            raise ValueError("resume_dataset_corpus_or_fixture_mismatch")
        if live and model_factory is None and parent["manifest"]["model"] != os.getenv("LLM_MODEL"):
            raise ValueError("resume_model_mismatch")
        selection = parent.get("selection", {})
        scenario_ids = selection.get("scenario_ids") or list(dict.fromkeys(r["scenario_id"] for r in previous))
        trials = selection.get("trials") or max(r["trial"] for r in previous)
    if scenario_ids:
        unknown = set(scenario_ids) - {s["id"] for s in scenarios}
        if unknown:
            raise ValueError("unknown_scenario_id")
        scenarios = [s for s in scenarios if s["id"] in scenario_ids]
    run_id = run_id or str(uuid4())
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", run_id):
        raise ValueError("invalid_run_id")
    target = Path(output_dir) / run_id
    target.mkdir(parents=True, exist_ok=False)  # Preserve earlier runs; never overwrite files.
    source_hash = source_fingerprint()
    effective_quota = quota_config or (quota_controller.config if quota_controller else QuotaConfig())
    report = {"schema_version": 2, "run_id": run_id, "mode": "live" if live else "scripted",
              "execution_status": "running", "created_at": datetime.now(timezone.utc).isoformat(),
              "live_gemini": "injected_test_adapter" if live and model_factory else "attempted_opt_in" if live else "not_run",
              "retrieval": "real_hybrid" if live or real_retrieval else "scripted_canonical_evidence",
              "selection": {"scenario_ids": [s["id"] for s in scenarios], "trials": trials},
              "quota_config": effective_quota.manifest() if live else None,
              "resume_parent": {"run_id": parent["run_id"], "source_sha256": parent["manifest"]["source_sha256"],
                                "source_changed": parent["manifest"]["source_sha256"] != source_hash,
                                "directory": str(Path(resume_from).resolve())} if parent else None,
              "manifest": {"dataset_sha256": dataset_hash,
                           "source_sha256": source_hash, "fixture_version": "stage3_demo_v1",
                           "corpus_fingerprint": corpus_hash, "python": platform.python_version(),
                           "sqlite": sqlite3.sqlite_version, "max_model_steps_per_turn": 8,
                           "intent_calls_per_turn": 1, "max_model_calls_per_turn": 9,
                           "max_proposal_recovery_attempts": 0,
                           "assignment_dispatch": "host_from_registered_intent",
                           "max_complete_assignment_model_calls_per_turn": 1,
                           "model": os.getenv("LLM_MODEL") if live else "ScriptedModel",
                           "embedding_model": EMBED_MODEL, "cross_encoder": RERANK_MODEL,
                           "sdk_total_attempts": 1 if live else None,
                           "versions": {p: importlib.metadata.version(p) for p in
                                        ("langgraph", "langchain-core", "langchain-google-genai", "google-genai", "pydantic", "numpy",
                                         "sentence-transformers", "transformers", "torch", "rank-bm25")}}}
    write_json(target / "run.json", report)
    (target / "trials").mkdir()
    results = resumable_results(previous)
    write_json(target / "carried_results.json", results)
    completed = {(r["scenario_id"], r["trial"]) for r in results}
    plan = [(s, t) for s in scenarios for t in range(1, trials + 1)]
    pending = [(s, t) for s, t in plan if (s["id"], t) not in completed]
    quota = (quota_controller or QuotaController(effective_quota)) if live else None
    run_events = []
    run_trace = Trace(None, observer=run_events.append, correlation_id=run_id + ":runner")
    base = None
    def retrieval_factory(path, trace):
        nonlocal base
        if not (live or real_retrieval):
            return ScriptedRetriever(corpus_dir=path, trace=trace)
        if Path(path) == DEFAULT_CORPUS:
            if base is None:
                base = PolicyRetriever(trace=trace)
            base.trace = trace
            return base
        if base is None:
            base = PolicyRetriever(trace=trace)
        base.prepare()
        return PolicyRetriever(corpus_dir=path, trace=trace, encoder=base.encoder, reranker=base.reranker)
    stop_reason = None
    try:
        for index, (scenario, trial) in enumerate(pending):
            if quota and index and index % quota.config.batch_size == 0:
                quota.wait(quota.config.batch_pause, run_trace, "scenario_batch")
                with (target / "runner_events.jsonl").open("a", encoding="utf-8") as stream:
                    for event in run_events:
                        stream.write(json.dumps(event) + "\n")
                run_events.clear()
            record = run_scenario(scenario, trial=trial, run_id=run_id, live=live,
                                        real_retrieval=real_retrieval, model_factory=model_factory,
                                        retriever_factory=retrieval_factory, quota=quota)
            write_json(target / "trials" / f"{scenario['id']}-{trial}.json", record)
            results.append(record)
            # Save completed trial events immediately, even if a later run is interrupted.
            with (target / "trajectories.jsonl").open("a", encoding="utf-8") as stream:
                for event in record["trajectory"]:
                    stream.write(json.dumps(event, ensure_ascii=False) + "\n")
            if quota and quota.halt_reason:
                stop_reason = quota.halt_reason
                break
    except KeyboardInterrupt:
        stop_reason = "operator_interrupted"
    results.sort(key=lambda r: ([s["id"] for s in scenarios].index(r["scenario_id"]), r["trial"]))
    resolved = {(r["scenario_id"], r["trial"]) for r in results if not trial_availability(r)["resume_incomplete"]}
    report.update(execution_status="paused" if stop_reason else "completed", stop_reason=stop_reason,
                  planned_trials=len(plan), attempted_trials=len(results),
                  pending_trials=[{"scenario_id": s["id"], "trial": t} for s, t in plan if (s["id"], t) not in resolved],
                  summary=aggregate(results), results=results)
    report["manifest"]["source_unchanged_during_run"] = source_fingerprint() == source_hash
    write_json(target / "results.json", report)
    with (target / "report.md").open("x", encoding="utf-8") as stream:
        stream.write(markdown_report(report))
    return report, target
