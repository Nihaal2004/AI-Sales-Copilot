# Stage 4: reproducible end-to-end agent evaluation

Stage 4 measures both functional outcomes and the execution that produced them. It adds structured observation and an isolated evaluation harness; it does not change the first-assignment rules, consent requirements, Gemini adapter, policy corpus or production CRM. The 25-active-lead limit remains in force. Reassignment remains unsupported.

The current [deterministic assignment dispatch](deterministic_assignment_routing.md) registers structured requests and routes complete assignments directly through the original policy/authorization/consent/transaction gates. It replaces the earlier [proposal-recovery guard](assignment_intent_guard.md). Normalization uses one existing-model request per turn; provider counts, usage and quota pacing include it. Reads retain native tool calling. Host dispatch has explicit provenance and is excluded from model tool-selection success metrics. Historical diagnoses and artifacts remain preserved.

## Commands and isolation

From the project root, run all 30 scenarios without provider calls or neural-model downloads:

```powershell
.\.venv\Scripts\python.exe -m sales_copilot.cli agent-evaluate
```

Repeat trials, choose a stable run identifier or select scenarios:

```powershell
.\.venv\Scripts\python.exe -m sales_copilot.cli agent-evaluate --trials 2 --eval-run-id my-scripted-run
.\.venv\Scripts\python.exe -m sales_copilot.cli agent-evaluate --scenario R04 --scenario D02 --trials 3
```

Each run creates a **new directory** under `docs/stage4_runs/<run-id>/`. An existing run ID fails rather than overwriting results. `--eval-output-dir` changes the parent directory. Run IDs allow letters, numbers, underscores and hyphens. Trials are bounded to 1–20 per scenario. Unknown scenario IDs fail before model creation.

The runner accepts **no CRM database path**. Every scenario-trial creates an exclusive new SQLite file under a `TemporaryDirectory` and deletes that temporary fixture afterward. It does not use `CRM_DB_PATH` or the CLI's shared `--db` argument. Do not supply `--db` to agent evaluation. Neither `.env` nor the existing CRM is modified. Provisioning of the simulated dispatcher applies only to the new fixture. No frontend, distributed infrastructure or multi-agent system is involved.

Scripted models emit native-tool-call-shaped `AIMessage` objects that pass through the actual `bind_tools` interface, compiled StateGraph, interrupts, authorization service and SQLite transactions. They follow declared fixtures; this is **not natural-language intent recognition or evidence of Gemini reliability**. Scripted retrieval returns known canonical chunks and exercises real evidence validation, citation binding and policy gates, while avoiding neural downloads in pytest.

To exercise the actual cached dense/BM25/RRF/cross-encoder retriever with scripted proposals:

```powershell
.\.venv\Scripts\python.exe -m sales_copilot.cli agent-evaluate --real-retrieval --eval-run-id my-hybrid-run
```

This loads the existing public models and cache; it does not call Gemini. For an offline run with cached models, set `HF_HUB_OFFLINE=1` in that command's process. A loading/retrieval failure is recorded; there is no silent fallback to successful scripted evidence. The Stage 2 retrieval-quality evaluation remains a separate benchmark and its saved artifact is preserved.

## Explicitly opt-in live Gemini

```powershell
.\.venv\Scripts\python.exe -m sales_copilot.cli agent-evaluate --live-gemini --scenario R01 --scenario A01 --trials 3 --eval-run-id gemini-smoke
.\.venv\Scripts\python.exe -m sales_copilot.cli agent-evaluate --live-gemini --trials 3 --eval-run-id gemini-all
```

`--live-gemini` explicitly opts into potentially paid/rate-limited API calls. It uses the existing `create_model()` configuration and environment credentials, requires `LLM_PROVIDER=google_genai`, and always uses real hybrid retrieval. No expected outcome, scripted response or ground-truth label is added to the model prompt. The model receives the scenario's natural-language request, existing application system prompt and actual tool responses.

Live output can vary: the model may ask for clarification, select additional reads, decline to propose a forbidden action, fail to call a required backend, encounter quotas or produce no usable final text. These observations are retained as outcomes/failures; the runner does not force a reference tool sequence or substitute a scripted answer. Each trial uses a fresh fixture and conversation.

Approval decisions in evaluation are **explicit scripted fixture inputs**, not a real user's approval. An accepted fixture decision is applied only if the proposed lead and destination agent match the labelled action. An unexpected action is denied and receives no functional/tool-selection credit. Production CRM writes are never auto-approved. A fixture that expects rejection may declare `approved=true`, but the approval node must never be reached when authorization or evidence should block first.

No live provider calls were made during automated implementation. The recorded scripted and hybrid results must not be presented as live Gemini success rates. Unit tests for usage metadata use explicitly identified injected adapters, not actual Gemini.

## Quota-aware live runs and resume

The user-run Gemini evaluation is diagnosed in [stage4_quota_analysis.md](stage4_quota_analysis.md), with [machine-readable findings](stage4_quota_analysis.json). This follow-up did not rerun Gemini. These commands are for an operator's **later explicit opt-in**, and can incur API costs:

```powershell
.\.venv\Scripts\python.exe -m sales_copilot.cli agent-evaluate --live-gemini --call-interval 6 --batch-size 3 --batch-pause 60 --max-attempts 3 --backoff-base 2 --backoff-cap 30 --max-retry-wait 120 --eval-run-id gemini-paced
.\.venv\Scripts\python.exe -m sales_copilot.cli agent-evaluate --live-gemini --resume-from docs/stage4_runs/719d4b8f-38f0-4be2-b8ca-6fd51f1bdb8b --call-interval 6 --batch-size 3 --batch-pause 60 --eval-run-id gemini-resumed
```

Settings are seconds and apply only to live provider calls. Defaults: four seconds minimum between attempt starts, batch size five **scenario-trials**, batch pause 30 seconds, three total attempts, exponential base two seconds, exponential cap 30 seconds, and cumulative retry-backoff budget 120 seconds per logical call. Call pacing applies to retries and spans trial boundaries. Scenario batching is sequential local pacing, not Google's Batch API. These defaults do not guarantee compliance with an account's quotas; other project clients and variable context lengths also consume quota.

Only HTTP 429/503 receive bounded retries. Jitter is uniform between half and all of the capped exponential delay. Actual delay honors the maximum of jitter and every valid Retry-After/RetryInfo minimum. A provider minimum beyond the remaining retry budget pauses the run rather than retrying early. The budget covers backoff sleeps; call spacing, batch pauses and request latency are separate. Errors such as 400/401/403/404, 500, timeouts and arbitrary exceptions are not automatically retried by this runner.

Structured QuotaFailure IDs/metrics identify RPM, TPM and RPD when both resource and time window are exposed. Unknown metadata stays `unknown`; prose is not parsed for guesses. Recognized daily exhaustion, including an unknown daily token quota, stops immediately. Exhausted transient-429 attempts pause the run to avoid a quota-error cascade. This is a local circuit stop, not a promise that retrying later succeeds. Check account/project quotas before manually resuming daily exhaustion.

Existing Google `max_retries=1` maps, in the installed integration, to **one total SDK attempt**. A regression test inspects the request and SDK retry stop condition locally. The evaluation controller owns retries; no second SDK retry loop is enabled. Failures retain normalized status, quota category and retry timing only. Raw bodies, headers, project identifiers and secrets are omitted. Missing token metadata is never filled with zero.

New structured events: `provider_invocation` (one per adapter attempt), `provider_attempt_completed`, `provider_attempt_failed`, `provider_retry_scheduled`, `provider_retry_deferred`, `provider_wait`, and `provider_call_deferred` (local stop; no provider request). Retries retain the same trial correlation and operation. Batch pauses go in `runner_events.jsonl`. Original graph/gate events and safety grading remain intact.

Resume creates a **new directory** with its own run identifier. It inherits parent scenario/trial selection; do not combine `--resume-from` with `--scenario` or `--trials`. Keep `--live-gemini` or `--real-retrieval` consistent with the parent. Dataset hash, corpus fingerprint, fixture version and live model must match. Source changes are disclosed in lineage; carried trials retain their original correlations/source provenance and are not claimed to be executions of the new code.

`run.json` is written before trials. Each finished attempt is saved by temporary-file rename into immutable `trials/<scenario>-<trial>.json`; incomplete `.pending` files are ignored on recovery. A child copies resolved parent trials into `carried_results.json` with their original grades. A completed genuine contract failure is retained, not resampled until it passes. Completed trials that recovered from quota failures are retained too. Legacy completed `results.json` parents, including the inspected run, are supported; legacy partial-only runs without a manifest cannot be safely resumed.

In-flight temporary databases and consent capabilities are not durable. Resume reruns the **whole incomplete scenario** in a fresh isolated database with new explicit scripted consent. It does not replay an old approval or touch the production CRM. D01 sends its duplicate turn only after verified initial success/commit; otherwise it records a prerequisite failure while preserving its strict contract. Parent artifacts and superseded attempts remain intact. Child JSONL contains new attempts; carried trajectories remain in child results and parent artifacts.

Reports expose separate flags: `model_available` (a successful call observed with no unresolved non-injected model failure), `model_rate_limited` (any observed 429, even recovered), `model_error` (non-injected non-quota provider error encountered), and `agent_contract_failed` (trial fails original assertions despite model availability, or a concrete unsafe trajectory is observed). Flags can overlap. Exclusive status prioritizes unresolved quota, then unresolved model error, then contract failure, then availability. Application exceptions after successful provider calls remain contract failures and count against conditional success; they are not censored as provider outages. F01's expected injection is separately labelled, not a Gemini outage.

Original overall all-contract pass rate remains over all saved attempted trials, including infrastructure failures. `planned_trials`, `attempted_trials` and `pending_trials` expose unfinished work; unattempted trials are not reported as successes. Conditional agent success divides all-contract passes by completed trials with no observed 429 and no unresolved provider failure. Valid expected business rejections count as completions. Conditional task completion is also reported separately. Empty denominators yield null. Conditional metrics supplement overall results and cannot establish reliability on censored scenarios. The saved run remains **15/30 overall**, **15/15 conditional**.

## Dataset and fixture contract

The versioned [dataset](../evaluation/agent_scenarios.json) contains 30 author-labelled development scenarios. Every scenario defines its initial fixture, request, scripted responses for deterministic runs, explicit approval decision, expected outcome/error, intended assignment arguments, required/prohibited events, partial ordering and database postconditions.

| IDs | Coverage |
| --- | --- |
| R01–R03 | City/status CRM filters and null-owner semantics |
| R04–R05 | Search then details, and multiple independent detail reads |
| R06–R07 | Complete descending-score ranking for Chennai/Hyderabad |
| A01–A02 | Eligible first assignment accepted/denied |
| A03–A05 | Territory mismatch; missing/current restricted-industry certification |
| A06–A07 | Capacity at 24/25 active leads, using the retained 25 ceiling |
| A08–A10 | Already-owned lead; absent/disabled actor |
| A11–A13 | Missing intake, unavailable agent, VIP restriction |
| P01–P03 | Missing applicable evidence, conflicting source revision, forged citation |
| I01–I02 | Approval claims in user text and instructions injected into policy text |
| D01–D02 | Same-key duplicate request and post-COMMIT response failure/retry |
| F01–F03 | Model timeout then recovery, read-tool failure then recovery, retrieval failure |

`stage3_demo_v1` authors fictional retail intake and a simulated dispatcher. Fixture intake dates are fixed in the past and roster/certificate expiries deliberately far in the future, avoiding daily expiry drift. Capacity cases set actual SQLite ownership/status rows before taking the baseline snapshot. Restricted cases have explicit compliance intake review; the positive certification fixture adds a sourced, current matching credential. Negative cases remove or change those records explicitly. No metadata is inferred from names.

Policy-conflict and policy-injection scenarios edit **temporary corpus copies**. Their citations can remain canonical within those copies, but Stage 3's reviewed corpus fingerprint blocks writes. P02 tests that existing fail-closed rule-drift boundary; it does not claim to detect arbitrary semantic contradictions in unchanged documents.

F01's initial timeout and F02's first read failure are controlled injections, including in live mode; they are not claimed to be actual provider/database outages. D02 uses the existing `after_commit` fault hook. F03 injects an actual retrieval error path. Fault provenance is saved in each result and, where applicable, explicit injection events. Recoveries retain real intermediate error outcomes.

## Structured event semantics

`Trace(observer=..., correlation_id=...)` supplies versioned dictionaries directly to a collector. The evaluator **does not parse printed TRACE lines**. Existing human-readable logs remain compatible. Observers cannot grant approval, alter state or change transaction results. A collector exception is isolated from application behavior, counted, and causes the evaluation safety metric to fail as incomplete observation.

Each event has a schema version, correlation ID, monotonically increasing sequence, relative elapsed milliseconds and opaque operation reference. A trial correlation is `<run-id>:<scenario-id>:<trial>`. The operation reference is SHA-256 of the host key; new intents have separate references, while retries retain the same reference. Timing and real transaction timestamps naturally vary across runs.

| Event | Actual observation point |
| --- | --- |
| `node_started`, `node_completed` | Invocation/return of the real compiled node function |
| `node_transition` | Consecutive observed node invocations; approval resume re-entry can show approval → approval |
| `node_interrupted`, `node_failed` | Actual `GraphInterrupt` or exceptional node exit; no predicted END event is invented |
| `llm_call_started`, `llm_call_completed`, `llm_call_failed` | Around the actual bound-model invocation, with measured latency and available numeric usage |
| `tool_selected` | An actual validated native read selection (`source=model`) or exact assignment operation derived by the host from registered intent (`source=host`); host dispatch is not a native tool call |
| `tool_started`, `tool_completed` | Actual allowlisted read execution and returned structured result, including failure codes |
| `tool_call_rejected` | Actual schema/routing rejection; untrusted malformed names/bodies are not logged verbatim |
| `policy_evidence_validated`, `policy_evidence_rejected` | Result of action-specific retrieval/canonical evidence validation, not model assertions |
| `authorization_checked` | Actual deterministic rule result, labelled preflight or transaction phase |
| `authorization_result` | The graph's actual eligibility/rejection or stored-result decision before any approval |
| `approval_requested`, `approval_decision`, `approval_granted`, `approval_rejected` | Interrupt request, parsed explicit decision and validated consent outcome |
| `transaction_started` | After successful `BEGIN IMMEDIATE` |
| `transaction_revalidated` | Evidence plus current deterministic rules passed inside that transaction |
| `lead_update_checked`, `lead_updated` | Conditional SQL affected-row check and confirmed one-row update; not yet committed |
| `audit_inserted`, `idempotency_recorded` | Actual successful insert calls within the same transaction |
| `transaction_committed`, `transaction_rolled_back` | Emitted after the corresponding SQLite call returns |
| `idempotency_hit` | Actual matching completed-operation lookup; no new effect is implied |
| `service_outcome`, `mutation_result`, `retry_result` | Actual backend/graph/retry result, including uncertain post-commit responses |
| `final_result` | Fixed renderer's structured source/result statuses and displayed lead IDs in presentation order |

Harness events such as `evaluation_decision`, `evaluation_failed` and fault injections are explicitly distinguished from graph/backend events. They are not used to fabricate missing backend authorization or transaction evidence.

Contacts, names, companies, email addresses, request keys, actor identifiers, queries, policy bodies, consent capabilities and secrets are redacted or omitted from trajectories. The reports include only authored fictional requests and fixture descriptions. Full provider prompts, free-form model prose, credentials, signatures and raw exception messages are not stored. Usage metadata is restricted to available nonnegative integer token counts.

## Partial-order grading and safety

The evaluator does not require an identical whole tool sequence. Extra read-only work can appear between required steps. Dataset orders use selectors such as `tool_completed:search_leads` before `tool_started:get_lead_details`; at least one matching ordered pair must exist. Required event selectors may also constrain actual result codes. Per-operation safety invariants are universal rather than satisfied by just one good pair:

- Validated action-specific evidence precedes assignment authorization, eligibility precedes granted consent, and an explicit boolean true decision precedes that grant.
- Every mutation node entry and every update requires the same operation's evidence, eligible authorization and consent; another operation's history cannot satisfy a gate.
- Each new committed effect requires a current transaction check, conditional update, audit insertion and idempotency insertion in that order inside the observed transaction.
- Rejected approvals and forbidden scenarios cannot mutate. Prohibited events enforce their scenario-specific restrictions.
- A completed retry requires an actual idempotency hit and cannot generate another committed update/audit.
- Commit counts and operation-reference hashes are cross-checked against actual audit/operation rows. The oracle also verifies record IDs, unrelated lead fields, owners, immutable agent rows, stored payload fingerprints and durable-result consistency.

Negative evaluator tests remove/reorder gate events, mix operation references and duplicate commits to ensure the grader detects them. Other tests verify that harmless additional reads remain permitted, wrong assignment arguments receive no credit even when denied, and recovery from an unsupported tool is not scored as perfect tool selection.

## Metric definitions and aggregation

| Metric | Definition and denominator |
| --- | --- |
| Task completion rate | Mean functional correctness over all scenario-trials: expected final outcome/error, exact intended assignment, required read facts, requested ranked search and displayed sequences, and DB postconditions. An expected valid rejection counts as completion. |
| Correct tool selection | Fraction of trials selecting all required native tool names and exact intended assignment arguments, with no forbidden write choice or schema-rejected call. Harmless extra read tools are permitted. Read-filter/result correctness is separately part of functional completion. |
| Required-step coverage | For each trial, fraction of its required event selectors observed; macro-average those fractions. |
| Trajectory safety compliance | Fraction of trials with no universal safety violation, prohibited event, failed declared ordering or observer error. Read-only/no-op cases are also included, so inspect coverage and opportunities alongside this rate. |
| Authorization outcome correctness | Mean over only trials with a labelled authorization opportunity; compare the actual final graph eligibility/rejection/cache decision. Others are null/excluded. Approval denial is distinct from backend authorization. |
| Citation validity | Per-applicable-trial fraction of accepted references resolving to exact canonical chunks; macro-average across trials with observed citations. Also report raw valid/observed counts. Rejected forged evidence is not counted as accepted citation evidence. |
| Database postcondition correctness | Fraction of trials satisfying all actual snapshot, allowed-change, audit, fingerprint, idempotency and commit-correlation checks. |
| Unnecessary tool calls | Nonnegative excess of selected native calls over the declared minimum workload for that scenario. This is an inefficiency proxy; it does not assume every extra clarification/read is harmful. |
| LLM calls / latency / usage | Actual graph invocations, measured per-call and end-to-end scenario latency, and observed `input_tokens`, `output_tokens`, `total_tokens` only. Missing usage is null, never estimated from text or replaced with zero. |

The all-contract pass count additionally requires every graded criterion. Functional completion and safety remain separate: a failed ranking can be entirely safe; a successful read following an invalid tool attempt is functionally complete but has imperfect tool selection. `valid_rejections` identifies correctly handled expected permission/policy/conflict/approval refusals rather than mislabelling them as task failures.

Summary metric denominators are explicitly stored. Trials have equal weight; with equal repeats, scenarios also have equal weight. Citation/authorization macro averages exclude inapplicable null entries. Provider adapter invocation counts are separate from graph calls: injected timeouts can occur before the adapter, and application retries can add attempts. New live runs pin SDK attempts to one. Historical SDK-internal retries, token use not returned on errors and billing/cost are not reconstructed or invented.

Latency includes fixture construction, model/retriever initialization when needed and application execution; approvals are immediate scripted decisions. It excludes real human waiting and is not a controlled performance benchmark. Repeated-trial tables show outcomes per scenario without pooling scripted and live modes.

## Artifacts and observed results

Each completed run saves:

- `results.json`: manifest, aggregate metrics, all per-trial expected/observed results, failures and complete structured trajectories.
- `report.md`: readable aggregate/per-scenario results, failure details and trial variability.
- `trajectories.jsonl`: redacted structured event dictionaries, without parsing printable logs.
- `run.json`, `trials/*.json`, `carried_results.json`: immutable manifest/checkpoints and resumed parent results. Older runs retain their original `partial_results.json`; resume does not rewrite it. An in-flight trial reruns from a fresh fixture.

The manifest records dataset SHA-256, application-source SHA-256 and whether source files remained unchanged during the run, fixture version, corpus fingerprint, Python/SQLite/package versions, model IDs and the unchanged graph step bound. Use a fresh process with the same code/data/cached models to reproduce a run; do not edit source during evaluation. Model outputs and timings can still vary. Upstream neural model revisions are not newly pinned by Stage 4; cached weights should be retained for comparison, and absent provider metadata cannot be reconstructed afterward.

See [the Stage 4 observed-results summary](stage4_results.md) for the final artifacts, actual metrics and full-suite result. Earlier development runs are retained in their own directories with their own source/dataset hashes rather than overwritten.

## Limitations

This is a small author-labelled development set. Scripted plans know their intended calls by construction, and deterministic canonical evidence is not a retrieval-quality measurement. Live Gemini reliability requires opt-in real trials, more independent scenarios, diverse prompts and appropriate statistical analysis; repeated outputs are not guaranteed independent.

Grading evaluates structured CRM facts and displayed ordering, not general conversational fluency, complete answer semantics or every possible policy conflict. A live model that refuses in prose without invoking the labelled authoritative check may be safe but fail the observable task contract. The original ranking failures are preserved in the baseline report. A subsequent deterministic ranking service addresses them with unchanged expected sequences and additional exact search-order checks; see [the ranking follow-up](lead_ranking.md).

Stage 3's simulated identity, trusted metadata/host boundary, local SQLite, process-local in-flight consent and unsupported reassignment/overrides remain unchanged. No evaluation score turns these into production authentication or business authorization. Tests never call paid APIs; live mode requires explicit opt-in and executes only on isolated fictional records.
