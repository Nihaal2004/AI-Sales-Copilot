# AI Sales Copilot — Stage 4

For a detailed code walkthrough and interview preparation, read the [interview-prep guide](docs/interview_prep_guide.md): architecture, file structure, request flows, retrieval, authorization, approval, transactions, evaluation, questions and study plan.

A Gemini-powered custom LangGraph agent over a fictional SQLite CRM. CRM reads and policy retrieval run automatically. **First-time lead assignment** requires grounded policy evidence, deterministic backend eligibility checks, an explicitly provisioned actor and separate human approval. SQLite atomically commits the assignment, audit and idempotency result.

Only unassigned active leads can receive an owner. Reassignment, unchanged-owner requests, VIP management workflows and all overrides are blocked. **Capacity remains 25 active leads**, as confirmed during implementation; 50 is an additional absolute maximum, not permission to exceed the current policy.

Complete assignment intents now use **deterministic host dispatch**: the host derives the exact candidate from the registered request and routes it through policy retrieval, authorization and human approval. Gemini does not need to repeat the action in an `assign_lead` tool call. Read-subject IDs grant no write capability; ambiguous requests clarify. See [the implementation and actual verification](docs/deterministic_assignment_routing.md) and [the preceding architecture assessment](docs/deterministic_assignment_routing_assessment.md).

## Verified results

| Check | Last recorded result | Evidence |
| --- | --- | --- |
| Full local test suite | **389 passed in 48.21 seconds** | [Validation details](docs/deterministic_assignment_routing.md) |
| Unchanged 30-scenario benchmark, two scripted trials each | **60/60 passed** | [Report](docs/stage4_runs/host-dispatch-scripted-final/report.md) |
| Scripted extraction/read model with cached local hybrid retrieval | **30/30 passed** | [Report](docs/stage4_runs/host-dispatch-hybrid-final/report.md) |
| A08/P01/P03/F03 host-dispatch regressions | **4/4 passed**, with zero native assignment calls or writes | [Tests](tests/test_assignment_guard_live_failures.py), [walkthrough](docs/deterministic_assignment_routing.md) |
| A03/A08 historical proposal plans supplied but never consumed | **2/2 passed**, with authoritative rejections and no writes | [Report and traces](docs/stage4_runs/host-dispatch-legacy-patterns-final/report.md) |

These follow-up results used isolated fictional SQLite databases and made **no Gemini API calls**. They verify application mechanics, not live model reliability. The earlier completed [gemini-full-30 live run](docs/stage4_runs/gemini-full-30/report.md) passed 28/30 before the guard; [its analysis](docs/live_gemini_failure_analysis.md) explains A03/A08 and the limits of the saved evidence. Historical runs remain unchanged.

The later saved [assignment-guard-full-live run](docs/stage4_runs/assignment-guard-full-live/report.md) remains **27/30**. Its P03/F01/F02 failures led to the targeted policy-read recovery, read-intent schema and timeout-injection fixes above. Raw intent JSON was absent from those saved traces; the local regressions demonstrate the schema defect without claiming to reconstruct missing Gemini output. No live benchmark was rerun.

The subsequently saved [final-live-regression run](docs/stage4_runs/final-live-regression/report.md) remains **26/30**. Its A08/P01/P03/F03 failures motivated host dispatch. The new offline passes do not replace that historical live result. Complete assignments now use one logical model request for normalization; proposal recovery has been removed. The 30-scenario scripted run records 53 logical model calls, compared with 75 previously.

## Reproducible agent evaluation (Stage 4)

Stage 4 adds **30 labelled end-to-end scenarios**, structured trajectory events, partial-order safety checks and real SQLite postconditions. It preserves the existing application and first-assignment rules. All evaluation databases are isolated fictional fixtures; the runner never uses your configured CRM database.

```powershell
# Deterministic workflow evaluation: no Gemini calls or model downloads.
.\.venv\Scripts\python.exe -m sales_copilot.cli agent-evaluate --trials 2
# Actual cached hybrid retrieval with scripted extraction/read responses; no Gemini calls.
.\.venv\Scripts\python.exe -m sales_copilot.cli agent-evaluate --real-retrieval
# Explicit opt-in to potentially paid Gemini calls, on isolated fixtures only.
.\.venv\Scripts\python.exe -m sales_copilot.cli agent-evaluate --live-gemini --scenario R01 --scenario A01 --trials 3
```

Runs save `results.json`, `report.md` and redacted `trajectories.jsonl` in a new `docs/stage4_runs/<run-id>/` directory. A pre-call `run.json`, immutable `trials/*.json` checkpoints and `carried_results.json` support resuming interrupted runs into a new directory. Older runs retain their original partial-results format. Use `--eval-run-id`, `--eval-output-dir`, repeated `--scenario` and `--trials` to control new runs. Existing run IDs are never overwritten. Approval decisions are explicitly scripted fixture inputs; production CRM writes are never auto-approved.

The evaluator reports task completion, correct tool selection/assignment arguments, required-step coverage, trajectory safety, authorization outcome, citation validity, database correctness, excess tool calls, LLM calls, latency and observed token usage. It separates `model_available`, `model_rate_limited`, `model_error` and `agent_contract_failed`, and retains both overall and conditional success rates. Unknown token usage is **null**, not invented. Valid expected rejections count as completed tasks. Extra harmless read calls are allowed, rather than requiring an identical entire tool sequence.

Check that the saved premature-final response plans remain unused by host dispatch, using a new output directory:

```powershell
.\.venv\Scripts\python.exe scripts/assignment_guard_replay.py --output-dir docs/stage4_runs/assignment-guard-replay-new
```

This regression preserves the original requests, fixtures, labels and graders. Historical native response plans are supplied but must never be invoked after complete intent registration; unavailable live message text is not reconstructed.

The original scripted baseline exposed **two ranking failures** caused by fixed ID ordering. Explicit deterministic ranking now addresses them; [current results](docs/ranking_results.md) retain the unchanged expected orders and stronger search/presentation assertions. Historical baseline artifacts remain preserved. Scripted results are not Gemini reliability measurements; no live API calls were triggered during automated implementation.

See [the complete walkthrough and exact CLI commands](docs/stage4_explained.md) and [observed results, artifacts and limitations](docs/stage4_results.md). The original Stage 2 retrieval evaluation remains separate and unchanged.

## Optional LangSmith tracing and offline Ragas evaluation

See [configuration, architecture, privacy boundaries and measured results](docs/observability_and_ragas.md).
LangSmith exports only operational metadata when `SALES_COPILOT_LANGSMITH=true` and
`LANGSMITH_API_KEY` are set. The existing structured traces remain independent.
The separate `python -m sales_copilot.rag_eval prepare` pipeline reuses the labelled
policy queries and cached models, creates a new artifact directory, and makes no paid
API calls. Ragas judging requires recorded responses and explicit `--allow-llm-judge`.

## Deterministic lead ranking

Ask “Rank all Chennai leads by score from highest to lowest.” The model can select `search_leads(sort_by="score", sort_order="desc")`. Ranked searches use recorded score descending, then recorded priority high/medium/low, then ID ascending. Ordinary searches keep their existing ID order. Search and final presentation preserve the same requested order.

`sort_by="business"` initially uses score with weight 1. Optional trusted host configuration supports explicit weights over recorded score/priority; no budget, engagement or other missing signals are invented. No scores, priorities or CRM records are backfilled. An explicit score request always uses score regardless of custom business weights.

```powershell
.\.venv\Scripts\python.exe -m sales_copilot.cli chat --thread-id ranking-demo --ranking-config config/lead_ranking.example.json
```

See [ranking semantics, configuration and limitations](docs/lead_ranking.md) and [the ranking implementation's recorded results](docs/ranking_results.md). Current guard verification is listed above. Ranking never grants assignment permission or bypasses retrieval, approval or idempotency.

## Existing CRM: preserve records and configuration

Run from the project directory with Python 3.11+ and the existing installed environment:

```powershell
cd "D:\Projects\AI Sales Copilot"
.\.venv\Scripts\python.exe -m sales_copilot.cli migrate
.\.venv\Scripts\python.exe -m sales_copilot.cli chat --thread-id stage3-existing
```

The migration is additive and repeatable. It never resets owners, updates lead timestamps, infers business metadata or grants permissions. Inspect current ownership through CRM read tools; already-owned leads reject new assignments, including requests naming their existing owner. Existing records and `.env` are preserved.

Chat reads Gemini credentials from the existing environment or `.env`; process environment variables take precedence. Configure `LLM_PROVIDER=google_genai`, an available `LLM_MODEL`, and `GOOGLE_API_KEY` or `GEMINI_API_KEY`. `GOOGLE_API_KEY` takes precedence when both keys are set. See [.env.example](.env.example) for names and optional settings; never replace a configured `.env` with that template.

The CLI has **no actor by default**: reads work and assignment fails closed. Missing industry, strategic designation, territory confirmation, roster or applicable certification also blocks. Nothing silently fills these gaps for existing records.

For an explicitly simulated identity on an existing database, provisioning is a separate opt-in command:

```powershell
.\.venv\Scripts\python.exe -m sales_copilot.cli setup-demo-identity
```

This creates only `demo-dispatcher`, with first-assignment permission and no manager role or override. It does not make already-owned leads assignable or supply their missing facts. Selecting it later requires `--demo-actor`. **This is fictional identity simulation, not production authentication.**

## Run an eligible assignment on a separate fictional demo CRM

```powershell
.\.venv\Scripts\python.exe -m sales_copilot.cli demo-db --db data/stage3_demo.sqlite3
.\.venv\Scripts\python.exe -m sales_copilot.cli chat --db data/stage3_demo.sqlite3 --demo-actor --thread-id stage3-demo
```

`demo-db` requires a new path and refuses to overwrite an existing file. It explicitly authors fictional retail intake and temporary roster facts and provisions the simulated dispatcher for that new fixture. Roster validity expires; choose a new separate demo file for another fresh fixture instead of resetting an existing one.

Example chat:

```text
You> Show unassigned Chennai leads.
You> What does the policy say about first assignment and agent capacity? Cite the passages.
You> Assign lead 21 to agent 1.
Approval required: [exact action, actor, operation ID and challenge displayed]
Type approve to execute; anything else rejects> approve
Copilot> SQLite confirmed: lead 21 assigned to agent 1.
You> Assign lead 21 to agent 4.
Copilot> assign_lead failed (lead_already_assigned). Only first-time assignment is supported. The existing owner was preserved.
```

The CLI displays canonical policy evidence before asking for approval. Only the separate exact `approve` input confirms; ordinary chat, policy text, citations and LLM assertions cannot approve or authorize a write. Any other approval input denies. The application checks business rules again inside the transaction after approval. No database transaction or lock is held while waiting for the human.

Each new chat intent gets a host-generated UUID. The model's request key is ignored. Keep the displayed operation ID for recovery; never create a new key automatically to retry the same intent.

- `/retry` retries/resolves the last uncertain approved operation in the same process, preserving its exact consent, action and key.
- `/quit` exits. In-memory graph checkpoints and consent signatures do not survive process restart.
- After restart, look up a committed result without executing anything:

```powershell
.\.venv\Scripts\python.exe -m sales_copilot.cli result --db data/stage3_demo.sqlite3 --demo-actor --operation-id ORIGINAL_UUID --lead-id 21 --assignee-id 1
```

A matching committed operation returns its prior result without another update or audit event. A mismatched payload conflicts. If no committed result exists after a restart, explicitly start chat with the **same** operation ID, propose the same action and obtain fresh approval:

```powershell
.\.venv\Scripts\python.exe -m sales_copilot.cli chat --db data/stage3_demo.sqlite3 --demo-actor --thread-id recovery-demo --operation-id ORIGINAL_UUID
```

`--operation-id` applies only to the first nonempty chat request; later new intents get fresh UUIDs. A retry is never automatically turned into a new logical operation. An ordinary new chat message is a new intent, so use the explicit recovery paths above.

## Tools and architecture

| Tool | Behavior |
| --- | --- |
| `search_leads(city, status, owner_id, unassigned_only, sort_by, sort_order)` | Read-only, optional ANDed filters; `owner_id=null` means any owner, `unassigned_only=true` means null owner |
| `get_lead_details(lead_id)` | Read-only details, including unassigned leads |
| `retrieve_policy(query, top_k)` | Canonical passages, exact versioned chunk citations and retrieval scores |
| `assign_lead(lead_id, assignee_id, request_key)` | Strict action schema; host derives it from registered intent. Legacy proposal callable cannot mutate and is not bound to the read model |

Assignment flow: `begin/structured intent + host candidate → policy_retrieval → authorization → approval/interrupt → mutation → AssignmentService → finish`. Reads retain `begin → agent → routing → read_tools → agent`, bounded by the step limit. No native assignment call, ToolMessage or tool-call ID is invented for host dispatch. There is no generic write ToolNode or edge that skips retrieval/consent. Finish still requires the exact action's authoritative outcome and renders verified results. See [deterministic dispatch](docs/deterministic_assignment_routing.md).

The service checks actor permission, lead/agent existence, null ownership, territory, verified intake, industry compliance review, current certification, VIP restrictions, roster/contact-window attestation and capacity. It pins reviewed policy mappings, failing closed on corpus drift. Missing facts block. See [the exact policy-to-rule mapping and implementation walkthrough](docs/stage3_explained.md).

A short `BEGIN IMMEDIATE` transaction rechecks current rules, counts active leads, conditionally updates only a null owner, confirms the affected row, and commits the audit and idempotency result atomically. Busy/locked errors are explicit bounded failures. There is no unsafe automatic retry loop. Structured outcomes are `success`, `already_completed`, `conflict`, `forbidden`, `policy_blocked`, `capacity_exceeded`, `temporary_failure`.

The completion guard separately reports `incomplete` or `clarification` when no exact-action disposition is available. Those outcomes are not authorization decisions or fabricated policy rejections. Use explicit numeric lead and agent IDs for assignment requests; missing or ambiguous IDs require clarification.

## Policy retrieval and evaluation

The unchanged corpus contains ten policies, 4,657 words and 60 section-preserving chunks. Retrieval still uses cached local `all-MiniLM-L6-v2` embeddings, BM25, RRF and the cross-encoder. Stage 3 does not implement the previous report's proposed retrieval-order change. Retrieval scores/citations remain evidence, never permission.

```powershell
.\.venv\Scripts\python.exe -m sales_copilot.cli prepare-policies
.\.venv\Scripts\python.exe -m sales_copilot.cli policy --query "Which industries need current certified agents?"
.\.venv\Scripts\python.exe -m sales_copilot.cli evaluate --output docs/stage2_evaluation_rerun.json
```

The policy command traces BM25, dense, RRF and reranked rankings. Contacts, consent capabilities and secrets are redacted. Credentials remain environment variables: `LLM_PROVIDER=google_genai`, configured `LLM_MODEL`, and `GOOGLE_API_KEY` or `GEMINI_API_KEY`. No new dependency is required for Stage 3.

The preserved [Stage 2 evaluation](docs/stage2_evaluation.json) reports Recall@3/MRR@5: BM25 0.928571/0.971429, dense 0.982143/0.964286, RRF 0.982143/1.000000, reranked 0.946429/0.982143. This historical small development-set evaluation was not rerun or relabelled for Stage 3. See [the error analysis](docs/stage2_error_analysis.md) for regressions, label gaps and aggregation caveats.

## Tests and actual traces

```powershell
.\.venv\Scripts\python.exe -m pytest
.\.venv\Scripts\python.exe scripts/stage3_trace_demo.py
```

The trace script uses real local retrieval models, compiled LangGraph and disposable SQLite databases. **Proposals and approvals are explicit scripted test inputs, not live Gemini responses or a real user's approval.** It records approval, all four failure boundaries, recovery and concurrent capacity competition in [stage3_actual_trace.jsonl](docs/stage3_actual_trace.jsonl).

The [recorded validation results](docs/deterministic_assignment_routing.md) include all Stage 1–3 permissions, certification, capacity, concurrency, idempotency, consent and transaction-failure checks, plus quota-aware evaluation, host provenance, cancellation, changed actions and approval replay regressions.

Structured request normalization uses the existing Gemini model once per chat turn. It registers explicit lead/destination IDs independently of native tool calls; this is untrusted task data, never authorization. Complete assignments dispatch immediately to policy validation and use no native-loop calls. Reads retain the default eight-call loop, making at most nine logical calls including normalization. Chat's bound is configurable using `MAX_AGENT_STEPS`; evaluation fixes it at eight. Quota pacing includes normalization. Missing/ambiguous IDs clarify; unexpected native assignment calls are rejected. Local scripted results do not establish live Gemini extraction reliability.

Live evaluation supports call/batch pacing, bounded 429/503 retries honoring provider retry advice, explicit daily-quota stopping, separate availability/contract metrics and immutable resumable trials. The historical [719d4b8f quota analysis](docs/stage4_quota_analysis.md) identified fifteen HTTP 429s and one deliberate timeout injection: 15/30 overall passes and 15/15 conditional passes on a censored sample. These figures describe that run, not the later live benchmark or the current guard. D01's duplicate probe requires an initial verified commit. See [exact controls, defaults and resume semantics](docs/stage4_explained.md#quota-aware-live-runs-and-resume).

For a **later operator-approved live resume** (incurs provider calls; not executed during implementation):

```powershell
.\.venv\Scripts\python.exe -m sales_copilot.cli agent-evaluate --live-gemini --resume-from docs/stage4_runs/719d4b8f-38f0-4be2-b8ca-6fd51f1bdb8b --call-interval 6 --batch-size 3 --batch-pause 60 --max-attempts 3 --eval-run-id gemini-resumed
```

Resume creates a new directory and fresh isolated databases for incomplete trials. Completed results, original artifacts, CRM data and `.env` are retained. The model, corpus, fixture version and dataset must match the parent; source differences are disclosed. Genuine completed contract failures are not automatically retried to improve scores.

## Scope and files

```text
sales_copilot/
  agent_eval/         # isolated fixtures, grading and scripted/opt-in live runners
  assignment.py       # authoritative transactional backend and bound consent
  authorization.py    # explicit structured policy rules
  migrations.py       # additive schema and explicit demo identity provisioning
  demo.py             # new-file-only fictional metadata fixture
  graph.py            # custom graph, retrieval, authorization and approval
  intent.py           # structured request extraction and measured model calls
  db.py               # read repository and service facade
  model.py            # existing Gemini adapter, unchanged
  schemas.py, tools.py, presentation.py, tracing.py
  corpus.py, retrieval.py, policy_gate.py, evaluation.py
  policies/           # ten unchanged versioned fictional policies
scripts/
  stage3_trace_demo.py
  assignment_guard_replay.py
  analyze_stage4_availability.py
tests/                # Stages 1–4, ranking, quotas and assignment-intent safety
README.md
docs/stage1_explained.md
docs/stage2_explained.md
docs/stage2_error_analysis.md
docs/stage3_explained.md
docs/stage3_actual_trace.jsonl
docs/stage4_explained.md
docs/live_gemini_failure_analysis.md
docs/assignment_intent_guard.md
docs/stage4_runs/      # preserved run manifests, reports and trajectories
```

The Stage 1/2 walkthroughs describe their historical guarantees; the Stage 3 walkthrough supersedes earlier statements that assignment writes lack deterministic authorization, concurrency protection or idempotency.

Limitations: simulated host identity, operator-authored metadata, local SQLite, process-local in-flight approval, no business calendar, no tamper-proof administrator audit, no reassignment/override or production authentication. Arbitrary SQL/hostile Python and other writers bypassing the assignment service are outside its boundary. No frontend, deployment, distributed queues or multi-agent architecture is included. Stage 4 evaluation is a local isolated harness.

## Fresh checkout only

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
if (!(Test-Path .env)) { Copy-Item .env.example .env }
# Configure your Gemini credentials in the new .env; never overwrite an existing one.
.\.venv\Scripts\python.exe -m sales_copilot.cli seed
.\.venv\Scripts\python.exe -m sales_copilot.cli migrate
```

Scientific-computing dependencies are used by the existing local retrieval models. `seed` remains non-destructive and does not reset an assigned demo lead; migration supplies no business metadata. For an eligible disposable fixture, use `demo-db` as shown above.
