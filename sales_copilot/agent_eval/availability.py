"""Infrastructure availability is separate from unchanged agent contract grades."""


def trial_availability(result):
    events = result["trajectory"]
    failures = [e for e in events if e["kind"] == "llm_call_failed"]
    # F01's first timeout is a declared harness fault, not a Gemini failure.
    injected = result.get("fault") == "model_timeout_once"
    genuine = []
    for event in failures:
        if injected and event.get("result", {}).get("error") == "model_timeout":
            injected = False
        else:
            genuine.append(event)
    attempts = [e for e in events if e["kind"] == "provider_attempt_failed"]
    rate = any(e.get("availability") == "model_rate_limited" for e in attempts) or any(
        e.get("result", {}).get("error") == "model_rate_limited" for e in genuine)
    error = any(e.get("availability") == "model_error" for e in attempts) or any(
        e.get("result", {}).get("error") != "model_rate_limited" for e in genuine)
    completed = any(e["kind"] == "llm_call_completed" for e in events)
    # An application exception after a successful model call must remain an
    # agent failure, not be censored as provider unavailability.
    available = completed and not genuine
    eligible = available and not rate
    # A concrete safety violation is still a contract failure during an outage.
    contract_failed = (available and not result["passed"]) or bool(result.get("safety_violations"))
    status = ("model_rate_limited" if rate and genuine else "model_error" if not available else
              "agent_contract_failed" if contract_failed else "model_available")
    quotas = {q for e in attempts for q in e.get("quota_types", [])}
    if rate and not quotas:
        quotas.add("unknown")
    return {"status": status, "model_available": available, "model_rate_limited": rate,
            "model_error": error, "agent_contract_failed": contract_failed,
            "conditional_eligible": eligible, "quota_types": sorted(quotas),
            "resume_incomplete": not available}


def availability_summary(results):
    classified = [(r, trial_availability(r)) for r in results]
    eligible = [r for r, a in classified if a["conditional_eligible"]]
    return {"trial_counts": {key: sum(a[key] for _, a in classified) for key in
                             ("model_available", "model_rate_limited", "model_error", "agent_contract_failed")},
            "status_counts": {key: sum(a["status"] == key for _, a in classified) for key in
                              ("model_available", "model_rate_limited", "model_error", "agent_contract_failed")},
            "overall_pass_rate": sum(r["passed"] for r in results) / len(results) if results else None,
            "conditional_agent_success": {"value": sum(r["passed"] for r in eligible) / len(eligible) if eligible else None,
                                           "numerator": sum(r["passed"] for r in eligible), "denominator": len(eligible)},
            "conditional_task_completion": {"value": sum(r["metrics"]["task_completion"] for r in eligible) / len(eligible) if eligible else None,
                                             "denominator": len(eligible)}}
