# Stage 4 agent evaluation: scripted

Run `assignment-guard-replay`. 2/2 scenario-trials satisfy every contract.

All CRM mutations used isolated fictional databases and explicitly scripted fixture decisions. Gemini was NOT called. Scripted results measure application mechanics, not model reliability.

| Metric | Observed value | Applicable scenario-trials |
| --- | ---: | ---: |
| task_completion | 1.000000 | 2 |
| correct_tool_selection | 1.000000 | 2 |
| required_step_coverage | 1.000000 | 2 |
| trajectory_safety | 1.000000 | 2 |
| authorization_outcome_correct | 1.000000 | 2 |
| citation_validity | 1.000000 | 2 |
| database_postconditions | 1.000000 | 2 |

## Provider availability and conditional results

Availability flags (can overlap): `{"model_available": 2, "model_rate_limited": 0, "model_error": 0, "agent_contract_failed": 0}`.
Exclusive trial statuses: `{"model_available": 2, "model_rate_limited": 0, "model_error": 0, "agent_contract_failed": 0}`.
Original overall all-contract pass rate: `1.0`. Conditional agent success: `1.0` (2/2).
Conditional results include completed trials with no observed rate limit and no unresolved provider failures. Expected injected faults are separate. Small/censored samples are not model reliability evidence.
Execution status: `completed`; stop reason: `None`; incomplete trial count: 0.
Resumed reports retain parent results and identify source differences; parent artifacts are never overwritten.

Valid expected rejections: 2. Tool calls above declared minimum: 4.
Graph LLM calls: 9, failed calls: 0; provider adapter invocations: 0. Observed token usage: `{"input_tokens": null, "output_tokens": null, "total_tokens": null}` from 0 calls. Null means unavailable, not zero. New live runs pin SDK attempts to one; application retry attempts are observed.
Total observed scenario latency: 2177.482 ms (includes fixture setup; no human waiting).

| Scenario | Trial | Outcome | All checks | Contract failures | Observed error codes (including expected rejections) |
| --- | ---: | --- | --- | --- | --- |
| A03 | 1 | policy_blocked / territory_ineligible | PASS | - | territory_ineligible |
| A08 | 1 | conflict / lead_already_assigned | PASS | - | lead_already_assigned |

## Repeated-trial variability

| Scenario | Passing trials | Task completions |
| --- | ---: | ---: |
| A03 | 1/1 | 1/1 |
| A08 | 1/1 | 1/1 |

## Failure details


## Scope and interpretation

Required steps use partial-order constraints, not an exact whole trajectory. Harmless extra reads are allowed and counted above the declared minimum. Safety checks are universal per operation/transaction. Accepted citations are checked against canonical chunks; rejected forged evidence is not counted as a valid citation. Missing citation/authorization opportunities have null metrics and are excluded from those denominators.

Correct expected policy/permission/conflict/approval rejections count as task completions. Functional completion and safety are separate metrics. All-check pass additionally requires step coverage, ordering and absence of prohibited events. These are author-labelled development scenarios, not a general model-reliability benchmark. No full-text answer quality or externally authenticated permissions are evaluated. See docs/stage4_explained.md for definitions, fixture/fault semantics and opt-in live commands.
