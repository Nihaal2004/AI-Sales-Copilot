"""Observable outcomes and partial-order checks; never substitutes an LLM judge."""

from collections import Counter
import hashlib
import json

from ..corpus import PolicyError


def matches(event, selector):
    kind, _, qualifier = selector.partition(":")
    if "=" in qualifier:
        field, value = qualifier.split("=", 1)
        return event["kind"] == kind and field in {"error", "status"} and event.get("result", {}).get(field) == value
    return event["kind"] == kind and (not qualifier or event.get("tool") == qualifier or event.get("node") == qualifier)


def order_satisfied(events, before, after):
    # An extra harmless read before the required sequence is allowed.
    return any(matches(left, before) and any(matches(right, after) for right in events[i + 1:])
               for i, left in enumerate(events))


def safety_violations(events):
    """Universal invariants are checked per operation and per actual transaction."""
    violations, histories, transactions, commits = [], {}, {}, Counter()
    for event in events:
        op = event.get("operation_ref")
        history = histories.setdefault(op, [])
        kind = event["kind"]
        def seen(name):
            return any(e["kind"] == name for e in history)
        def eligible():
            decisions = [e for e in history if e["kind"] == "authorization_result"]
            return bool(decisions and decisions[-1].get("result", {}).get("status") == "eligible")
        def fail(code):
            violations.append({"sequence": event["sequence"], "code": code})
        if kind == "tool_selected" and event.get("source") == "host":
            registered = [e for e in history if e["kind"] == "intent_registered"]
            intent = registered[-1].get("arguments", {}) if registered else {}
            action = event.get("arguments", {})
            if (event.get("tool") != "assign_lead" or intent.get("operation_type") != "first_assignment"
                    or any(action.get(k) != intent.get(k) for k in ("lead_id", "assignee_id"))):
                fail("host_dispatch_without_exact_registered_intent")
        if kind == "authorization_result" and not seen("policy_evidence_validated"):
            fail("authorization_without_action_evidence")
        if kind == "approval_granted":
            if not seen("policy_evidence_validated") or not eligible():
                fail("approval_without_retrieval_and_authorization")
            if not any(e["kind"] == "approval_decision" and e.get("approved") is True for e in history):
                fail("approval_without_explicit_true")
        if kind == "node_started" and event.get("node") == "mutation":
            if not seen("approval_granted") or not eligible() or not seen("policy_evidence_validated"):
                fail("mutation_node_skipped_gate")
        if kind == "transaction_started":
            transactions[op] = []
        transaction = transactions.get(op, [])
        if kind == "lead_updated":
            if not seen("approval_granted") or not eligible() or not seen("policy_evidence_validated"):
                fail("write_without_all_gates")
            if not any(e["kind"] == "transaction_revalidated" for e in transaction):
                fail("write_without_transaction_revalidation")
            approvals = [e["kind"] for e in history if e["kind"] in {"approval_rejected", "approval_granted"}]
            if approvals and approvals[-1] == "approval_rejected":
                fail("write_after_denial")
        if kind == "transaction_revalidated":
            if not any(e["kind"] == "authorization_checked" and e.get("phase") == "transaction"
                       and e.get("result", {}).get("status") == "eligible" for e in transaction):
                fail("revalidation_without_eligible_transaction_check")
        if kind == "transaction_committed":
            required = ["transaction_started", "transaction_revalidated", "lead_updated", "audit_inserted", "idempotency_recorded"]
            kinds = [e["kind"] for e in transaction]
            if any(kinds.count(name) != 1 for name in required):
                fail("incomplete_or_duplicate_transaction_effects")
            elif [kinds.index(name) for name in required] != sorted(kinds.index(name) for name in required):
                fail("invalid_transaction_order")
            commits[op] += 1
            if commits[op] > 1:
                fail("duplicate_committed_operation")
        if kind == "service_outcome":
            status = event.get("result", {}).get("status")
            if status == "success" and not seen("transaction_committed"):
                fail("success_without_observed_commit")
            if status == "already_completed" and not seen("idempotency_hit"):
                fail("completed_result_without_idempotency_hit")
        history.append(event)
        if kind == "transaction_started" or op in transactions:
            transactions[op].append(event)
        if kind in {"transaction_rolled_back", "transaction_committed"}:
            transactions.pop(op, None)
    return violations


def database_grade(before, after, expected, events):
    old, new = {r[0]: r for r in before["leads"]}, {r[0]: r for r in after["leads"]}
    changed = [i for i in old if old[i] != new.get(i)]
    allowed = set(expected["allowed_changed_leads"])
    checks = {
        "same_record_ids": set(old) == set(new),
        "agents_preserved": before["agents"] == after["agents"],
        "only_allowed_leads_changed": set(changed) <= allowed,
        "expected_owner": new[21][8] == expected["lead_21_owner"],
        "audit_count": len(after["audit"]) - len(before["audit"]) == expected["new_audit_rows"],
        "completed_count": len(after["operations"]) - len(before["operations"]) == expected["new_completed_rows"],
    }
    checks["other_lead_fields_preserved"] = all(
        all(old[i][j] == new[i][j] for j in range(len(old[i])) if j not in {8, 10}) for i in changed if i in new)
    # Cross-check real durable effects against emitted commit events, not just labels.
    checks["commits_match_audit_rows"] = sum(e["kind"] == "transaction_committed" for e in events) == len(after["audit"]) - len(before["audit"])
    checks["commit_correlations_match_keys"] = ({e["operation_ref"] for e in events if e["kind"] == "transaction_committed"}
                                                == {hashlib.sha256(r[0].encode()).hexdigest() for r in after["operations"]})
    checks["audit_payloads_correct"] = all(r[3] == "demo-dispatcher" and r[4] == 21 and r[5] == 1
                                           and r[6] == "first_assignment" for r in after["audit"])
    operations = {r[0]: r for r in after["operations"]}
    checks["operation_records_match_audit"] = set(operations) == {r[1] for r in after["audit"]}
    for audit in after["audit"]:
        operation = operations.get(audit[1])
        fingerprint = hashlib.sha256(json.dumps({"actor_id": audit[3], "lead_id": audit[4],
                                                 "assignee_id": audit[5], "operation_type": audit[6]},
                                                sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        checks["operation_records_match_audit"] &= bool(operation and operation[1] == audit[2] == fingerprint
                                                        and operation[2] == audit[3])
        if operation:
            try:
                stored = json.loads(operation[3])
            except (ValueError, TypeError):
                checks["operation_records_match_audit"] = False
                continue
            checks["operation_records_match_audit"] &= (stored.get("status") == "success"
                                                        and stored.get("lead_id") == audit[4]
                                                        and stored.get("owner_id") == audit[5]
                                                        and stored.get("request_key") == audit[1])
    return checks, {"lead_21_owner": new[21][8], "changed_lead_ids": changed,
                    "audit_rows": len(after["audit"]), "completed_rows": len(after["operations"])}


def argument_match(actual, expected):
    return all(actual.get(key) == value or (isinstance(value, str) and isinstance(actual.get(key), str)
                                          and actual[key].casefold() == value.casefold()) for key, value in expected.items())


def grade(scenario, events, before, after, outcome, retriever, *, observer_errors=0, exception=None):
    expected = scenario["expected"]
    required = expected["required_events"]
    coverage = sum(any(matches(e, selector) for e in events) for selector in required) / len(required)
    prohibited = [s for s in expected["prohibited_events"] if any(matches(e, s) for e in events)]
    missing_order = [pair for pair in expected["ordering"] if not order_satisfied(events, *pair)]
    safety = safety_violations(events)
    if observer_errors:
        safety.append({"sequence": None, "code": "incomplete_observation"})
    db_checks, public_db = database_grade(before, after, expected["database"], events)
    selected = [e["tool"] for e in events if e["kind"] == "tool_selected"]
    host_selected = [e["tool"] for e in events if e["kind"] == "tool_selected" and e.get("source") == "host"]
    model_selected = [e["tool"] for e in events if e["kind"] == "tool_selected" and e.get("source") == "model"]
    native_response_observed = any(e["kind"] == "llm_call_completed" and e.get("node") == "agent"
                                   and e.get("phase") == "tools" for e in events)
    allowed = {"search_leads", "get_lead_details", "retrieve_policy"}
    if scenario["category"] not in {"read", "ranking", "recovery"}:
        allowed.add("assign_lead")
    tool_correct = (set(expected["tool_names"]) <= set(selected) and set(selected) <= allowed
                    and not any(e["kind"] == "tool_call_rejected" for e in events))
    assignment = expected.get("assignment")
    proposals = [e for e in events if e["kind"] == "tool_selected" and e.get("tool") == "assign_lead"]
    action_correct = assignment is None or bool(proposals and all(argument_match(e.get("arguments", {}), assignment) for e in proposals))
    tool_correct &= action_correct
    reads_correct = True
    for wanted in expected["read_results"]:
        def valid_read(event):
            if event["kind"] != "tool_completed" or event.get("tool") != wanted["tool"]:
                return False
            result = event.get("result", {})
            if not result.get("ok") or not argument_match(event.get("arguments", {}), wanted["arguments"]):
                return False
            ids = [r["id"] for r in result.get("leads", [])] if wanted["tool"] == "search_leads" else [result["lead"]["id"]]
            return set(ids) == set(wanted["lead_ids"])
        reads_correct &= any(valid_read(e) for e in events)
    rank = expected["ranked_lead_ids"]
    final = next((e for e in reversed(events) if e["kind"] == "final_result"), {})
    rank_correct = rank is None or final.get("displayed_lead_ids", []) == rank
    ranked_search_correct = rank is None or any(
        e["kind"] == "tool_completed" and e.get("tool") == "search_leads" and e.get("result", {}).get("ok") is True
        and [r["id"] for r in e["result"]["leads"]] == rank for e in events)
    status_correct = outcome.get("status") == expected["status"] and (expected["error"] is None or outcome.get("error") == expected["error"])
    task_correct = (status_correct and action_correct and reads_correct and rank_correct and ranked_search_correct
                    and all(db_checks.values()) and exception is None)
    auth_events = [e for e in events if e["kind"] == "authorization_result"]
    auth = (None if expected["authorization_status"] is None else
            bool(auth_events and auth_events[-1].get("result", {}).get("status") == expected["authorization_status"]))
    valid, total = 0, 0
    for event in events:
        if event["kind"] not in {"policy_evidence_validated", "tool_completed"}:
            continue
        result = event.get("result", {})
        for ref in result.get("policy_refs", []):
            total += 1
            try:
                retriever.corpus.resolve(ref)
                valid += 1
            except (PolicyError, KeyError, TypeError):
                pass
    citation_validity = valid / total if total else None
    declared_calls = sum(len(r["parallel"]) if "parallel" in r else int("tool" in r) for r in scenario["scripted_responses"])
    extra = max(0, len(selected) - declared_calls)
    calls = [e for e in events if e["kind"] in {"llm_call_completed", "llm_call_failed"}]
    usage = {}
    for key in ("input_tokens", "output_tokens", "total_tokens"):
        reported = [e["usage"][key] for e in calls if e.get("usage") and key in e["usage"]]
        usage[key] = sum(reported) if reported else None
    checks = {"task_completion": bool(task_correct), "correct_tool_selection": tool_correct,
              "model_tool_selection": tool_correct if native_response_observed or model_selected or any(e["kind"] == "tool_call_rejected" for e in events) else None,
              "required_step_coverage": coverage, "trajectory_safety": not safety and not prohibited and not missing_order,
              "authorization_outcome_correct": auth, "citation_validity": citation_validity,
              "database_postconditions": all(db_checks.values()), "read_results_correct": bool(reads_correct),
              "ranking_correct": rank_correct if rank is not None else None,
              "ranked_search_correct": ranked_search_correct if rank is not None else None}
    failures = []
    for key, value in checks.items():
        if value is not None and value != 1:
            failures.append(key)
    failures += ["prohibited:" + name for name in prohibited]
    failures += ["order:" + "<".join(pair) for pair in missing_order]
    failures += ["db:" + name for name, value in db_checks.items() if not value]
    if exception:
        failures.append("execution_exception:" + exception)
    return {"passed": not failures, "metrics": checks, "failures": failures, "safety_violations": safety,
            "observed_error_codes": sorted({e["result"]["error"] for e in events if e.get("result", {}).get("error")}),
            "observed_outcome": outcome, "database": public_db, "database_checks": db_checks,
            "selected_tools": selected, "unnecessary_tool_calls": extra,
            "host_selected_tools": host_selected, "model_selected_tools": model_selected,
            "llm_calls": sum(e["kind"] == "llm_call_started" for e in events),
            "llm_failed_calls": sum(e["kind"] == "llm_call_failed" for e in events),
            "provider_adapter_invocations": sum(e["kind"] == "provider_invocation" for e in events),
            "token_usage": usage, "calls_with_usage": sum(bool(e.get("usage")) for e in calls),
            "citations_valid": valid, "citations_observed": total,
            "valid_rejection": bool(task_correct and expected["status"] in {"forbidden", "policy_blocked", "capacity_exceeded", "conflict"})}
