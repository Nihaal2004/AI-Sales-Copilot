# Assignment-intent completion guard

**Historical implementation:** the [deterministic host-dispatch follow-up](deterministic_assignment_routing.md) now routes complete intents directly into policy retrieval and removes proposal recovery. This document retains the earlier design and its recorded results; see the follow-up for current state/provenance, tests and execution paths.

This implements the targeted follow-up to [the gemini-full-30 analysis](live_gemini_failure_analysis.md). A03/A08 could previously finish after successful reads without obtaining an assignment decision. The existing nine graph nodes, assignment service, policy rules, approval interrupt and SQLite executor remain in place. The later [P03/F01/F02 investigation and fixes](assignment_guard_failure_fixes.md) document bounded recovery after policy-read errors, non-authorizing read-subject IDs and phase-specific evaluation timeout injection.

## Request registration and completion

`begin` now requests one native structured response from the **existing chat model**, using `with_structured_output(RequestIntent, method="json_schema", include_raw=True)`. This is request normalization, not another agent or action planner. It extracts operation kind and explicit numeric lead/destination IDs, without executing tools or evaluating eligibility. Production does not use a keyword router. The regular Gemini `bind_tools` loop still chooses and issues actual CRM tool calls.

The normalizer receives only the current user request and, for the same host operation ID, the previously registered assignment. It does not receive retrieved documents, old tool outputs, permissions, evaluation labels or scripted answers. Missing/ambiguous IDs require clarification; names alone do not cause guessed IDs. Unknown fields such as `approved`, model-provided operation keys, invalid types and malformed structured output fail closed. Extraction is untrusted: it cannot authorize, approve or execute anything.

The host binds validated slots into `AssignmentIntent(operation_type="first_assignment", lead_id, assignee_id, operation_id)`. The operation ID is generated/supplied by the host, never by the model. State independently retains:

- `assignment_intent`: the exact request requiring a disposition.
- `resolved_action`: the action for which the existing policy, authorization, approval or mutation path actually returned an outcome.
- `recovery_used` / `recovery_pending`: the one logical proposal-recovery allowance.
- `completion_result`: explicit unresolved/clarification state, separate from business authorization results.

A read may retain explicit subject IDs in the untrusted extraction response. Only `first_assignment` registers an action; read IDs never permit an assignment proposal or authorize execution. Strict types and forbidden extra fields remain enforced.

A native assignment proposal must match the registered lead, destination and host key. A changed action, or an assignment proposed during a registered read request, ends as incomplete before policy/approval/mutation. Existing model request keys remain untrusted and are replaced with the host key. New user intents receive new keys. An explicitly same-key retry can retain its prior registered target; changing that target requires clarification rather than reinterpreting the key.

## Bounded recovery and existing safety gates

When a tool-free final would finish an unresolved assignment, routing can request **one** proposal-recovery response, within the existing eight-call tool-loop budget. The invocation-only system instruction asks for exactly the registered `assign_lead` proposal for backend validation, even for apparently ineligible territory or an already-owned lead. It does not grant execution permission and is not stored as a persistent system instruction in chat history.

A failed advisory policy read during a registered assignment also triggers that same single allowance, before repeated reads exhaust the budget. This does not accept the failed evidence or fabricate a disposition. The real recovered proposal must enter `policy_retrieval`, which independently retrieves and validates evidence. Repeated finals, changed actions or errors during recovery still finish incomplete.

The accepted native proposal follows the existing path:

```text
registered intent → ordinary native-tool/read loop
  → premature final → at most one native proposal recovery
  → existing routing → policy_retrieval → authorization
      → authoritative rejection → finish
      → approval interrupt → explicit bound decision → mutation → finish
```

There is no edge from recovery to mutation or approval. `assign_lead` remains a proposal-only tool whose body cannot write. Policy retrieval stays action-bound; deterministic authorization, actor identity, signed consent, transactional rechecks, conditional first-assignment updates, audit and idempotency still belong to the original service. Capacity remains 25. Existing assignment/authorization/evidence service files and policies were preserved.

Another tool-free final, a read instead of a recovery proposal, malformed/changed arguments, unavailable provider or exhausted budget produces explicit `incomplete`/`clarification`. No business rejection or success is invented. Actual provider/tool errors remain in results and traces. A model error is not automatically repaired with a new action or key. Routing replies only to real native tool calls; a tool-free final never causes a synthesized ToolMessage or fabricated assignment call.

The finish node independently checks exact-action resolution. Successful reads cannot satisfy the assignment contract. The renderer explains incomplete/clarification results, and the evaluator prioritizes these results over `read_success`. An actual backend temporary failure remains its authoritative uncertain outcome, with the original same-operation retry path.

## Observability, cost and bounds

Intent extraction adds **one provider request per chat turn**, including read turns. The unchanged tool-loop limit is eight model invocations, including any proposal recovery; total logical model calls are therefore bounded by nine per turn. Existing quota pacing, SDK single-attempt settings and bounded provider retries also cover normalization. HTTP retry attempts are distinct from the one logical recovery response.

Structured events identify `phase=intent`, `phase=tools`, and `phase=proposal_recovery`. `intent_registered`, `assignment_recovery_requested` and `assignment_incomplete` expose the guard's decisions. Host operation keys are redacted in argument traces; correlation/operation digests remain stable. Available usage is read from the structured response's raw AIMessage; missing usage stays null. No model prose or provider secrets are added to artifacts.

The normalizer is still a model interpretation of natural language and can misclassify a request. These local scripted results establish the state/gate mechanics, not Gemini extraction quality or a new live success rate. Clarification may be needed for names, pronouns on a new operation or compound requests. No live Gemini call was made for this implementation.

## Actual local evaluations

The original dataset/labels remain SHA-256 `b67a4b655b0c3406c0676a2bcb8e90eb591402b85530bed782beb1d9763a0557`. Previous live and scripted artifacts were not overwritten.

| Evaluation | Observed result | Artifact |
| --- | --- | --- |
| All 30 scenarios, two scripted trials each | **60/60** all-contract passes; database and trajectory safety 60/60; zero provider calls | [report](stage4_runs/assignment-guard-scripted/report.md), [JSON](stage4_runs/assignment-guard-scripted/results.json) |
| All 30 scenarios, scripted proposals with actual cached hybrid retrieval, offline | **30/30** all-contract passes; database and trajectory safety 30/30; zero provider calls | [report](stage4_runs/assignment-guard-hybrid/report.md), [JSON](stage4_runs/assignment-guard-hybrid/results.json) |
| Saved A03/A08 premature-final shapes plus a scripted recovery proposal | **2/2** all-contract passes; no approvals or writes | [report](stage4_runs/assignment-guard-replay/report.md), [JSON](stage4_runs/assignment-guard-replay/results.json), [actual structured trajectories](stage4_runs/assignment-guard-replay/trajectories.jsonl) |

Replay A03: intent registered at event 5; recovery requested at 42; authoritative `policy_blocked / territory_ineligible` at 60. Replay A08: intent at 5; recovery at 31; authoritative `conflict / lead_already_assigned` at 49. Both fixtures retain zero changed leads, audit rows and completed operations. Policy query text and final AIMessage prose were absent/redacted in the original live artifacts; the replay reproduces their native-call shapes with declared fictional replacements, not their unavailable text or a new Gemini response.

The standard single-trial scripted suite now records 75 logical model calls: the original 43 tool-loop calls plus 32 per-turn normalizations. Two benchmark follow-ups explain the extra two turns. The two-repeat artifact records 150 calls and two deliberate F01 timeout injections; observed token usage is null because the scripted model supplies none.

Commands used locally (no Gemini):

```powershell
.\.venv\Scripts\python.exe -m pytest -o addopts='' -q
.\.venv\Scripts\python.exe -m sales_copilot.cli agent-evaluate --trials 2 --eval-run-id assignment-guard-scripted
.\.venv\Scripts\python.exe scripts/assignment_guard_replay.py
$env:HF_HUB_OFFLINE='1'
.\.venv\Scripts\python.exe -m sales_copilot.cli agent-evaluate --real-retrieval --eval-run-id assignment-guard-hybrid
```

Run IDs/output directories are exclusive. Use a new ID or `--output-dir` for a later replay; existing artifacts are never reset.

New regression tests reproduce both native-call patterns and verify exact backend denials, one recovery only, explicit consent for eligible recovered proposals, missing/ambiguous IDs, malformed extraction, read-only questions, unrequested/changed actions, malicious approval claims, provider errors, step limits, per-turn reset, same-key retries and exact-action finish checks. Offline model doubles implement structured extraction separately from native responses; their simple authored command matching is evaluation-only code and not production routing.

Earlier safety tests often scripted lead 21 → agent 1 while saying “Assign” or requesting different IDs. Those prompts now state the scripted action explicitly; their safety assertions remain. Separate ambiguity and mismatch regressions require clarification/incomplete results. Observed-call count and quota-spacing assertions now include normalization. The old assertion that invented final prose yields generic usage guidance now requires explicit incomplete assignment. No benchmark expectations or safety graders were relaxed.

Final full test suite: **346 passed in 143.25 seconds**. An initial full run found one remaining seed-upgrade test whose prompt named a different agent from its scripted proposal; its input was aligned with the intended action and the complete suite rerun. All earlier tests remain, with thirty new guard cases. The offline hybrid run loaded both existing model caches successfully; its Hub-unavailable messages reflect deliberate offline mode, not a retrieval fallback. These are local scripted results, not new live Gemini measurements.

Preservation checks confirmed unchanged `.env`, existing CRM bytes, original dataset, policy documents, assignment/authorization/evidence service files and every pre-existing evaluation artifact. New run directories contain separate reports and JSONL trajectories.
