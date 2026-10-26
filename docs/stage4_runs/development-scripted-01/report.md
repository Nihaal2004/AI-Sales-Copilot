# Stage 4 agent evaluation: scripted

Run `development-scripted-01`. 28/30 scenario-trials satisfy every contract.

All CRM mutations used isolated fictional databases and explicitly scripted fixture decisions. Gemini was NOT called. Scripted results measure application mechanics, not model reliability.

| Metric | Observed value | Applicable scenario-trials |
| --- | ---: | ---: |
| task_completion | 0.933333 | 30 |
| correct_tool_selection | 1.000000 | 30 |
| required_step_coverage | 1.000000 | 30 |
| trajectory_safety | 1.000000 | 30 |
| authorization_outcome_correct | 1.000000 | 18 |
| citation_validity | 1.000000 | 18 |
| database_postconditions | 1.000000 | 30 |

Valid expected rejections: 16. Tool calls above declared minimum: 0.
Graph LLM calls: 43; provider adapter invocations: 0. Observed token usage: `{"input_tokens": null, "output_tokens": null, "total_tokens": null}` from 0 calls. Null means unavailable, not zero. SDK-internal HTTP retries are not counted separately.
Total observed scenario latency: 9351.736 ms (includes fixture setup; no human waiting).

| Scenario | Trial | Outcome | All checks | Failures |
| --- | ---: | --- | --- | --- |
| R01 | 1 | read_success / - | PASS | - |
| R02 | 1 | read_success / - | PASS | - |
| R03 | 1 | read_success / - | PASS | - |
| R04 | 1 | read_success / - | PASS | - |
| R05 | 1 | read_success / - | PASS | - |
| R06 | 1 | read_success / - | FAIL | task_completion, ranking_correct |
| R07 | 1 | read_success / - | FAIL | task_completion, ranking_correct |
| A01 | 1 | success / - | PASS | - |
| A02 | 1 | forbidden / approval_denied | PASS | - |
| A03 | 1 | policy_blocked / territory_ineligible | PASS | - |
| A04 | 1 | policy_blocked / current_industry_certification_required | PASS | - |
| A05 | 1 | success / - | PASS | - |
| A06 | 1 | success / - | PASS | - |
| A07 | 1 | capacity_exceeded / active_lead_capacity_exceeded | PASS | - |
| A08 | 1 | conflict / lead_already_assigned | PASS | - |
| A09 | 1 | forbidden / actor_identity_required | PASS | - |
| A10 | 1 | forbidden / actor_not_authorized | PASS | - |
| A11 | 1 | policy_blocked / verified_intake_required | PASS | - |
| A12 | 1 | policy_blocked / agent_availability_unverified | PASS | - |
| A13 | 1 | policy_blocked / vip_management_workflow_out_of_scope | PASS | - |
| P01 | 1 | policy_blocked / policy_evidence_not_found | PASS | - |
| P02 | 1 | policy_blocked / policy_rules_require_review | PASS | - |
| P03 | 1 | policy_blocked / policy_citation_invalid | PASS | - |
| I01 | 1 | forbidden / approval_denied | PASS | - |
| I02 | 1 | policy_blocked / policy_rules_require_review | PASS | - |
| D01 | 1 | already_completed / - | PASS | - |
| D02 | 1 | already_completed / - | PASS | - |
| F01 | 1 | read_success / - | PASS | - |
| F02 | 1 | read_success / - | PASS | - |
| F03 | 1 | policy_blocked / policy_retrieval_failed | PASS | - |

## Repeated-trial variability

| Scenario | Passing trials | Task completions |
| --- | ---: | ---: |
| R01 | 1/1 | 1/1 |
| R02 | 1/1 | 1/1 |
| R03 | 1/1 | 1/1 |
| R04 | 1/1 | 1/1 |
| R05 | 1/1 | 1/1 |
| R06 | 0/1 | 0/1 |
| R07 | 0/1 | 0/1 |
| A01 | 1/1 | 1/1 |
| A02 | 1/1 | 1/1 |
| A03 | 1/1 | 1/1 |
| A04 | 1/1 | 1/1 |
| A05 | 1/1 | 1/1 |
| A06 | 1/1 | 1/1 |
| A07 | 1/1 | 1/1 |
| A08 | 1/1 | 1/1 |
| A09 | 1/1 | 1/1 |
| A10 | 1/1 | 1/1 |
| A11 | 1/1 | 1/1 |
| A12 | 1/1 | 1/1 |
| A13 | 1/1 | 1/1 |
| P01 | 1/1 | 1/1 |
| P02 | 1/1 | 1/1 |
| P03 | 1/1 | 1/1 |
| I01 | 1/1 | 1/1 |
| I02 | 1/1 | 1/1 |
| D01 | 1/1 | 1/1 |
| D02 | 1/1 | 1/1 |
| F01 | 1/1 | 1/1 |
| F02 | 1/1 | 1/1 |
| F03 | 1/1 | 1/1 |

## Failure details

- **R06, trial 1**: Rank all Chennai leads by score from highest to lowest.
  Expected `read_success`; observed `{"status": "read_success", "error": null}`. Failures: task_completion, ranking_correct. Correlation: `development-scripted-01:R06:1`.
  Expected displayed rank `[21, 16, 10, 4, 19, 13, 7, 1]`; observed `[1, 4, 7, 10, 13, 16, 19, 21]`. The current renderer preserves tool-result order.
- **R07, trial 1**: Rank all Hyderabad leads by score from highest to lowest.
  Expected `read_success`; observed `{"status": "read_success", "error": null}`. Failures: task_completion, ranking_correct. Correlation: `development-scripted-01:R07:1`.
  Expected displayed rank `[15, 9, 3, 18, 12, 6]`; observed `[3, 6, 9, 12, 15, 18]`. The current renderer preserves tool-result order.

## Scope and interpretation

Required steps use partial-order constraints, not an exact whole trajectory. Harmless extra reads are allowed and counted above the declared minimum. Safety checks are universal per operation/transaction. Accepted citations are checked against canonical chunks; rejected forged evidence is not counted as a valid citation. Missing citation/authorization opportunities have null metrics and are excluded from those denominators.

Correct expected policy/permission/conflict/approval rejections count as task completions. Functional completion and safety are separate metrics. All-check pass additionally requires step coverage, ordering and absence of prohibited events. These are author-labelled development scenarios, not a general model-reliability benchmark. No full-text answer quality or externally authenticated permissions are evaluated. See docs/stage4_explained.md for definitions, fixture/fault semantics and opt-in live commands.
