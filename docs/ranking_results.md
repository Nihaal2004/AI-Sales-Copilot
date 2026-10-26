# Lead ranking: observed results

The two Stage 4 ranking failures are resolved by an explicit deterministic search-order service. The initial business strategy uses the recorded integer score, with recorded priority then ID as tie-breaks. It does not invent business signals or populate new scoring factors in existing records. Ordinary searches retain ID order. [Configuration and limitations](lead_ranking.md) describe the optional weighted strategy and exact tool arguments.

## Validation

- **Full suite: 279 passed in 71.25 seconds.** All existing tests remain, with the formerly expected R06/R07 failures now required to pass. Forty additional tests cover ranking/configuration, native search/presentation, missing signals and negative evaluation checks. `pip check` found no broken requirements.
- **30 scenarios, two scripted trials each: 60/60 fully passing**, up from 56/60 in the preserved pre-ranking baseline.
- **30 scenarios with actual cached hybrid retrieval: 30/30 fully passing**, up from 28/30. The agent proposals remain scripted; no live Gemini reliability claim is made.
- Both search-result order and the exact displayed sequence are now required to match. Removing the ranking tool argument or corrupting just the returned search order still fails evaluation.

| Scenario | Actual ordered search IDs | Actual displayed IDs |
| --- | --- | --- |
| R06: Chennai, score descending | 21, 16, 10, 4, 19, 13, 7, 1 | 21, 16, 10, 4, 19, 13, 7, 1 |
| R07: Hyderabad, score descending | 15, 9, 3, 18, 12, 6 | 15, 9, 3, 18, 12, 6 |

The original expected sequences, requests, fixture definitions, database postconditions and authorization/approval/trajectory requirements were compared with the preserved baseline and remain identical. Only the scripted ranking calls gained the new sort arguments, and their expected read arguments now additionally require `sort_by=score`. The evaluator gained an exact search-order check alongside its existing final-order check. No assertions were weakened to obtain these results.

## Benchmark metrics

| Metric | Scripted evidence, 60 trials | Real hybrid retrieval, 30 trials |
| --- | ---: | ---: |
| Task completion | 60/60 | 30/30 |
| Correct tool selection | 60/60 | 30/30 |
| Required-step coverage | 100% | 100% |
| Trajectory safety | 60/60 | 30/30 |
| Correct applicable authorization outcomes | 36/36 | 18/18 |
| Accepted canonical citation references | 190/190 | 95/95 |
| Database postconditions | 60/60 | 30/30 |
| Valid expected rejections | 32 | 16 |
| Calls above the declared tool minimum | 0 | 0 |
| Scripted model calls | 86 | 43 |
| Deliberately injected model timeouts | 2 | 1 |
| Live provider adapter invocations | 0 | 0 |

Model token usage is **null** because scripted models provide none. The injected read failure, timeout and post-commit response uncertainty remain visible in the report, even though their recovery scenarios pass. Each idempotent retry still has exactly one committed assignment/audit/result. Observed total scenario latency was 25.468 seconds for the two-trial scripted run and 79.147 seconds for the real-retrieval run, including fixtures and local model loading. These are not controlled performance comparisons. Offline cached model loading succeeded with cache/deprecation notices and no download.

## Artifacts and reproduction

- [Scripted repeated-trial report](stage4_runs/ranking-score-scripted/report.md), [JSON results](stage4_runs/ranking-score-scripted/results.json), [structured trajectories](stage4_runs/ranking-score-scripted/trajectories.jsonl).
- [Actual-retrieval report](stage4_runs/ranking-score-hybrid/report.md), [JSON results](stage4_runs/ranking-score-hybrid/results.json), [structured trajectories](stage4_runs/ranking-score-hybrid/trajectories.jsonl).
- [Preserved pre-ranking Stage 4 baseline](stage4_results.md).

```powershell
.\.venv\Scripts\python.exe -m pytest
.\.venv\Scripts\python.exe -m sales_copilot.cli agent-evaluate --trials 2
.\.venv\Scripts\python.exe -m sales_copilot.cli agent-evaluate --real-retrieval
```

Each benchmark creates new isolated fictional databases and a new run directory. Existing run artifacts are preserved. The source/dataset hashes and current exact search/display sequences were validated against the saved results. Source files remained unchanged during each final run.

## Preservation and remaining limits

The existing CRM database, `.env`, saved Stage 2 evaluation, policy corpus, first-assignment service, deterministic authorization rules, policy gate and Gemini adapter were verified unchanged. No migration, record reset, score/priority update, new fictional factor backfill or permission grant was performed. Ranking does not confer assignment eligibility or bypass explicit approval/idempotency.

Weights are explicit operator choices, not a calibrated predictive model. Missing business signals remain missing. Future factors need verified data, reviewed extractors and defined units. Natural-language tool selection by live Gemini was not evaluated here; the existing `--live-gemini` opt-in remains available on isolated fixtures.
