# Stage 4 agent evaluation: live

Run `deterministic-routing-full-live`. 30/30 scenario-trials satisfy every contract.

All CRM mutations used isolated fictional databases and explicitly scripted fixture decisions. This opt-in run selected Gemini; consult provider invocation counts and failures. Outputs can vary across trials.

| Metric | Observed value | Applicable scenario-trials |
| --- | ---: | ---: |
| task_completion | 1.000000 | 30 |
| correct_tool_selection | 1.000000 | 30 |
| model_tool_selection | 1.000000 | 9 |
| required_step_coverage | 1.000000 | 30 |
| trajectory_safety | 1.000000 | 30 |
| authorization_outcome_correct | 1.000000 | 18 |
| citation_validity | 1.000000 | 18 |
| database_postconditions | 1.000000 | 30 |

`correct_tool_selection` measures application operation selection, including explicitly traced host dispatch. `model_tool_selection` applies to trials with an observed native-loop response or native selection/rejection, including omitted required tools; host-only assignments are excluded, not counted as LLM tool-calling successes. Historical events without provenance are not relabelled.
Host-dispatched actions: 22. Explicitly model-selected tools: 12.

## Provider availability and conditional results

Availability flags (can overlap): `{"model_available": 30, "model_rate_limited": 0, "model_error": 0, "agent_contract_failed": 0}`.
Exclusive trial statuses: `{"model_available": 30, "model_rate_limited": 0, "model_error": 0, "agent_contract_failed": 0}`.
Original overall all-contract pass rate: `1.0`. Conditional agent success: `1.0` (30/30).
Conditional results include completed trials with no observed rate limit and no unresolved provider failures. Expected injected faults are separate. Small/censored samples are not model reliability evidence.
Execution status: `completed`; stop reason: `None`; incomplete trial count: 0.
Resumed reports retain parent results and identify source differences; parent artifacts are never overwritten.

Valid expected rejections: 16. Tool calls above declared minimum: 0.
Graph LLM calls: 52, failed calls: 1; provider adapter invocations: 51. Observed token usage: `{"input_tokens": 28075, "output_tokens": 3080, "total_tokens": 31155}` from 51 calls. Null means unavailable, not zero. New live runs pin SDK attempts to one; application retry attempts are observed.
Total observed scenario latency: 314816.837 ms (includes fixture setup; no human waiting).

| Scenario | Trial | Outcome | All checks | Contract failures | Observed error codes (including expected rejections) |
| --- | ---: | --- | --- | --- | --- |
| R01 | 1 | read_success / - | PASS | - | - |
| R02 | 1 | read_success / - | PASS | - | - |
| R03 | 1 | read_success / - | PASS | - | - |
| R04 | 1 | read_success / - | PASS | - | - |
| R05 | 1 | read_success / - | PASS | - | - |
| R06 | 1 | read_success / - | PASS | - | - |
| R07 | 1 | read_success / - | PASS | - | - |
| A01 | 1 | success / - | PASS | - | - |
| A02 | 1 | forbidden / approval_denied | PASS | - | approval_denied |
| A03 | 1 | policy_blocked / territory_ineligible | PASS | - | territory_ineligible |
| A04 | 1 | policy_blocked / current_industry_certification_required | PASS | - | current_industry_certification_required |
| A05 | 1 | success / - | PASS | - | - |
| A06 | 1 | success / - | PASS | - | - |
| A07 | 1 | capacity_exceeded / active_lead_capacity_exceeded | PASS | - | active_lead_capacity_exceeded |
| A08 | 1 | conflict / lead_already_assigned | PASS | - | lead_already_assigned |
| A09 | 1 | forbidden / actor_identity_required | PASS | - | actor_identity_required |
| A10 | 1 | forbidden / actor_not_authorized | PASS | - | actor_not_authorized |
| A11 | 1 | policy_blocked / verified_intake_required | PASS | - | verified_intake_required |
| A12 | 1 | policy_blocked / agent_availability_unverified | PASS | - | agent_availability_unverified |
| A13 | 1 | policy_blocked / vip_management_workflow_out_of_scope | PASS | - | vip_management_workflow_out_of_scope |
| P01 | 1 | policy_blocked / policy_evidence_not_found | PASS | - | policy_evidence_not_found |
| P02 | 1 | policy_blocked / policy_rules_require_review | PASS | - | policy_rules_require_review |
| P03 | 1 | policy_blocked / policy_citation_invalid | PASS | - | policy_citation_invalid |
| I01 | 1 | forbidden / approval_denied | PASS | - | approval_denied |
| I02 | 1 | policy_blocked / policy_rules_require_review | PASS | - | policy_rules_require_review |
| D01 | 1 | already_completed / - | PASS | - | - |
| D02 | 1 | already_completed / - | PASS | - | commit_response_unknown |
| F01 | 1 | read_success / - | PASS | - | model_timeout |
| F02 | 1 | read_success / - | PASS | - | read_failed |
| F03 | 1 | policy_blocked / policy_retrieval_failed | PASS | - | policy_retrieval_failed |

## Repeated-trial variability

| Scenario | Passing trials | Task completions |
| --- | ---: | ---: |
| R01 | 1/1 | 1/1 |
| R02 | 1/1 | 1/1 |
| R03 | 1/1 | 1/1 |
| R04 | 1/1 | 1/1 |
| R05 | 1/1 | 1/1 |
| R06 | 1/1 | 1/1 |
| R07 | 1/1 | 1/1 |
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


## Scope and interpretation

Required steps use partial-order constraints, not an exact whole trajectory. Harmless extra reads are allowed and counted above the declared minimum. Safety checks are universal per operation/transaction. Accepted citations are checked against canonical chunks; rejected forged evidence is not counted as a valid citation. Missing citation/authorization opportunities have null metrics and are excluded from those denominators.

Correct expected policy/permission/conflict/approval rejections count as task completions. Functional completion and safety are separate metrics. All-check pass additionally requires step coverage, ordering and absence of prohibited events. These are author-labelled development scenarios, not a general model-reliability benchmark. No full-text answer quality or externally authenticated permissions are evaluated. See docs/stage4_explained.md for definitions, fixture/fault semantics and opt-in live commands.
