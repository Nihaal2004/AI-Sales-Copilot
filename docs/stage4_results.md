# Stage 4 observed results

This is the preserved **pre-ranking baseline**. See [the ranking follow-up](ranking_results.md) for current runs after implementing explicit deterministic ranking. Original metrics and run artifacts below remain unchanged.

The final runs evaluate all **30 labelled scenarios** on isolated fictional SQLite databases. Both use scripted native-tool-capable agent models and explicitly scripted fixture decisions. **Live Gemini was not run**; no model-reliability or live success-rate claim follows from these results.

| Run | Trials per scenario | All-contract passes | Task completion | Trajectory safety | DB postconditions |
| --- | ---: | ---: | ---: | ---: | ---: |
| Scripted agent + canonical evidence fixture | 2 | 56/60 | 93.3333% | 60/60 | 60/60 |
| Scripted agent + actual cached hybrid retrieval | 1 | 28/30 | 93.3333% | 30/30 | 30/30 |
| Live Gemini | Not run | Not measured | Not measured | Not measured | Not measured |

The scripted two-trial run had correct tool selection and required-step coverage on 60/60 scenario-trials. Authorization was correct on **36/36 applicable trials**, and **190/190 observed accepted citation references** resolved canonically across 36 citation-bearing trials. It counted **32 valid expected rejections**, zero tool calls above the declared minimum, 86 graph model calls and two deliberately injected model timeouts. Rejections were counted as successful expected outcomes rather than task failures.

The real-retrieval run had correct tool selection/coverage on 30/30 trials, authorization correct on **18/18 applicable trials**, and **95/95 accepted citation references** valid across 18 citation-bearing trials. It counted 16 valid rejections, zero excess calls, 43 scripted model calls and one deliberate timeout. It used real local dense embeddings, BM25, RRF, cross-encoder reranking and current canonical citation validation; it does not test a real model's tool-selection ability.

No provider metadata was available from the scripted models: input/output/total token usage is **null**, not zero or an estimate. Provider adapter invocations were zero in both recorded runs. Observed total scenario latency was **24.606 seconds** for the scripted two-trial run and **49.419 seconds** for the real-retrieval run. These include fixture setup and, for real retrieval, local model loading; they are not comparable performance benchmarks. Offline cached model loading succeeded, emitting cache/deprecation notices without a download or hosted embedding call.

## Actual failures

Only **R06 and R07** failed, in both scripted repeats and in the real-retrieval run:

| Scenario | Requested descending-score order | Actual displayed order |
| --- | --- | --- |
| R06: Chennai ranking | 21, 16, 10, 4, 19, 13, 7, 1 | 1, 4, 7, 10, 13, 16, 19, 21 |
| R07: Hyderabad ranking | 15, 9, 3, 18, 12, 6 | 3, 6, 9, 12, 15, 18 |

The read repository orders results by ID, and the final renderer preserves those results rather than displaying a model-authored reordered answer. The evaluator checks the order actually shown to the user. These are functional ranking failures with safe, unchanged database state. Production behavior was not changed to make the scores pass.

Intermediate errors remain visible even when a scenario passes: F01 records `model_timeout` then successful read recovery; F02 records `read_failed` then a verified successful retry; D02 records `commit_response_unknown` after the actual commit, then `already_completed` on a same-key retry. These codes appear in each run's readable per-scenario table and complete raw structured trajectory. The latter has exactly one committed lead update, audit event and operation result.

## Artifacts

- [Scripted repeated-trial report](stage4_runs/stage4-final-scripted/report.md), [machine-readable results](stage4_runs/stage4-final-scripted/results.json), [structured events](stage4_runs/stage4-final-scripted/trajectories.jsonl).
- [Actual-retrieval report](stage4_runs/stage4-final-hybrid/report.md), [machine-readable results](stage4_runs/stage4-final-hybrid/results.json), [structured events](stage4_runs/stage4-final-hybrid/trajectories.jsonl).
- [Explicit live-not-run status](stage4_live_status.json).
- [Definitions, scenario descriptions, exact commands and limitations](stage4_explained.md).

Dataset/source hashes match the current files and source remained unchanged during both final runs. Summary denominators and macro averages were independently recomputed from stored per-trial values using rational arithmetic; all match. JSONL trajectories match the event arrays in the JSON results. Earlier development artifacts remain separately identified by their own hashes/run IDs rather than overwritten.

## Regression and preservation

The final full suite passed **239 tests in 72.93 seconds**, including all **177 Stage 1–3 tests**. No previous tests were deleted or rewritten for Stage 4. New evaluator tests cover all 30 fixtures, missing/reordered gates, duplicate commits, cross-operation consent, harmless extra reads, wrong action arguments, observed faults/recovery, usage metadata, redaction, isolated databases and the opt-in boundary. `pip check` reported no broken requirements.

Existing CRM bytes, `.env`, the policy corpus and saved Stage 2 evaluation were verified unchanged. No real CRM assignment, schema reset, reassignment, permission grant or business metadata backfill was performed by Stage 4. Authorization/approval decisions remain under the original Stage 3 rules; production changes only add observations and the isolated evaluation command.

This is a small author-labelled development set. Scripted success is not evidence of Gemini reliability. Run explicit live repeated trials before making claims about natural-language tool selection, model variability or API reliability. No provider cost, missing usage or unobserved safety guarantee is invented.
