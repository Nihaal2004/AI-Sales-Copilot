# Deterministic lead ranking

The original Stage 4 R06/R07 failures came from `CRM.search_leads` always returning `ORDER BY id`. The fixed renderer correctly preserved that list, so a model-written ranking never became the displayed answer. Search now exposes explicit ranking arguments backed by a pure Python service; presentation preserves the returned order.

## Available facts and initial strategy

Inspection of the existing `leads` table found `id`, contact/company fields, `city`, `status`, integer `score` (0–100), recorded `priority` (`low`, `medium`, `high`), nullable `owner_id`, and creation/update timestamps. The current database has 21 leads. No budget, engagement, conversion probability, estimated revenue or customer lifetime value is recorded. Assignment intake/certification/roster tables support eligibility checks, not an invented measure of lead value.

The default ranked strategy is:

1. Recorded numerical **score descending**.
2. Equal scores: recorded **priority high → medium → low**.
3. Still equal: **lead ID ascending**.

Nothing recalculates the stored score or priority. Priority often follows score in the original seed, so it is a tie-break by default rather than an additional weighted contribution. Status, ownership and city remain explicit search filters. Closed or already-owned leads are not silently excluded from a request for *all* leads, and ranking does not establish assignment eligibility. Timestamps are not used as a proxy for buying intent or engagement.

For ascending ranking, only the primary score/weighted total changes direction. Priority ties remain high first and final ID ties remain ascending. This avoids nondeterministic order and accidentally reversing tie-breaks.

Ordinary searches retain their existing ID-ascending default. Ranking requires a structured tool argument selected by the actual model; no keyword-only intent router was added.

## Tool and host API

```python
search_leads(city="Chennai", sort_by="score", sort_order="desc")
search_leads(city="Hyderabad", sort_by="score", sort_order="asc")
search_leads(city="Chennai", unassigned_only=True, sort_by="business")
```

`sort_by` is one of `id`, `score`, `business`. If `sort_order` is omitted/null, ID ordering defaults ascending and ranked ordering defaults descending. Explicit `score` requests always use the recorded score as the primary criterion, even if a host has customized business weights. The host may disable the priority tie-break; ID remains the final deterministic tie-break.

`CRM.search_leads(SearchLeads(...))` returns an ordered list. The native tool additionally returns compact ranking metadata naming the actual factors, weights, direction and tie-breaks. The renderer uses that list directly and displays its ranking basis. It does not reorder by ID, trust model-authored sorting claims or add derived scoring fields to the CRM records. Structured traces retain ranking arguments/metadata while continuing to redact contacts and secrets.

## Configurable business strategy

`sales_copilot/ranking.py` separates validated configuration, factor extractors and deterministic ordering. `business` defaults to a single score factor with weight 1, giving the same initial ranking as `score`. The trusted host can supply a `RankingConfig` to `CRM`; the language model cannot supply weights in tool arguments.

The default [example configuration](../config/lead_ranking.example.json) is:

```json
{
  "factors": [{"field": "score", "weight": "1"}],
  "priority_tiebreak": true
}
```

Load it explicitly during chat:

```powershell
.\.venv\Scripts\python.exe -m sales_copilot.cli chat --thread-id ranking-demo --ranking-config config/lead_ranking.example.json
```

Example requests:

- “Rank all Chennai leads by score from highest to lowest.”
- “Rank all Hyderabad leads by score from lowest to highest.”
- “Rank unassigned Chennai leads using the configured business ranking.”

Only `score` and `priority` have implemented extractors. An operator may explicitly add a weighted priority factor if they choose that business rule. For that optional arithmetic factor, priority is an ordinal mapping: low=0, medium=1, high=2. That mapping is a declared ranking rule over an existing category, not a measured conversion probability or a new stored business fact. The total is `sum(weight × extracted value)`; units are not silently normalized or empirically calibrated. No non-score factor is active by default.

Weights are finite nonnegative Decimals, bounded to 10,000 with at most six decimal places. Configurations require unique supported fields and at least one positive weight. Empty/all-zero configurations, duplicates, unknown factors, extra keys, negative/non-finite weights and excessive precision fail validation. Invalid JSON/file contents produce a fixed safe error; configuration text is not logged or sent to the model. Configuration is opt-in, does not modify `.env`, and does not write to the CRM.

The extractor registry and weighted composition support future reviewed factors. Adding one requires an actual verified data source, explicit semantics/units, configuration-schema and extractor changes, tests and business review. A name in JSON cannot invent a signal. Missing required score/priority values, invalid current-schema values or non-unique IDs cause a ranking error instead of imputing zero, guessing values, skipping records or returning a partial ranking. Zero-weight supported factors are unused; priority is still required when its tie-break is enabled.

## Preserved safety boundary

Ranking is a read-only ordering operation after parameterized SQL filtering. It neither grants permissions nor chooses an owner automatically. First-assignment authorization, the 25-active-lead ceiling, action-bound policy evidence, explicit approval interrupt, transactional rechecks, audit/idempotency and post-commit retries retain their existing behavior. No schema migration or score backfill was added. The existing CRM and `.env` are preserved byte-for-byte.

## Evaluation and regression

R06/R07 retain their original natural-language requests and exact expected lead orders:

| Scenario | Expected score-descending IDs |
| --- | --- |
| R06: Chennai | 21, 16, 10, 4, 19, 13, 7, 1 |
| R07: Hyderabad | 15, 9, 3, 18, 12, 6 |

The scripted reference calls now explicitly request `sort_by=score`, matching the natural-language request and new native-tool capability. The expected read arguments additionally require score ranking. Expected IDs, database postconditions, authorization, approval, citation and trajectory checks were not relaxed. The evaluator now requires **both the search result order and the displayed order** to equal the exact expected sequence. New negative tests deliberately omit ranking arguments or corrupt just the tool-result order and confirm evaluation still fails.

Regression tests also cover ascending/descending order, priority/ID ties, input-order independence, custom/Decimal weights, score requests ignoring custom business weights, invalid/missing signals, filters, native-tool binding, final presentation, trace metadata, CLI configuration, unranked compatibility and unchanged database bytes.

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest
.\.venv\Scripts\python.exe -m sales_copilot.cli agent-evaluate --trials 2
.\.venv\Scripts\python.exe -m sales_copilot.cli agent-evaluate --real-retrieval
```

The latter two use isolated fictional databases and scripted models. The second benchmark variant uses the actual local hybrid retriever. Neither calls Gemini. Real natural-language model selection remains measurable through the existing explicit `--live-gemini` opt-in; scripted success is not a claim of Gemini reliability.

Final observed results and artifact links are recorded in [ranking_results.md](ranking_results.md). The original [Stage 4 baseline](stage4_results.md) and its machine-readable failed runs remain preserved as historical evidence.
