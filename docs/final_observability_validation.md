# Final observability and Ragas validation

Run `20261026-7b84c120`; recorded 2026-10-26T05:39:24.346951+00:00. Real Gemini requests were explicitly authorized for this validation.

## 1. LangSmith hosted verification

**Hosted delivery is unverified.** Both credentials were configured without being displayed. The configured project is `sales-copilot` at the default `https://api.smith.langchain.com` endpoint. Authenticated SDK project lookup, run listing and direct `Client.read_run` all returned **HTTP 403 Forbidden**. The unauthenticated SDK `/info` endpoint returned HTTP 200, so the host is reachable. Account/workspace/project permissions or an authenticated-request restriction remain unresolved; the evidence does not establish which one. An attempted check outside the sandbox could not start because the execution helper reported `helper_unknown_error: setup refresh had errors`.

Independent hosted records read: **0**. Project UUID, hosted parent relationships, hosted latency and hosted token fields could not be confirmed. No dashboard verification was possible. Zero synchronous exporter exceptions are not evidence of hosted delivery; asynchronous upload acceptance is also unverified.

The real Gemini-backed demonstration did complete against a **new, isolated fictional SQLite database**. A read used `get_lead_details` and `retrieve_policy` and left ownership unchanged. A separate request reached the human-approval interrupt; the explicitly approved fictional assignment committed successfully. The existing host approval API, authorization and transaction paths were used without modification.

| Local graph invocation | Root run / trace ID | Duration (ms) |
| --- | --- | ---: |
| Read | `9ae7e943-1236-4152-a0f3-74c02229e41f` | 14693.473 |
| Assignment proposal / approval interrupt | `77795006-7d84-4a96-b4f8-cb6b284895a7` | 4896.986 |
| Approved resume / commit | `364cf7ab-1e00-402b-bf76-d96a55cefdb4` | 42.735 |

**30 outbound records were inspected**, with valid local parent/trace/dotted-order relationships: **True**. These are locally observed outbound spans, not independently observed hosted spans.

| Span | Count |
| --- | ---: |
| `graph.invoke` | 3 |
| `llm.intent` | 2 |
| `llm.tools` | 2 |
| `node.agent` | 2 |
| `node.approval` | 2 |
| `node.authorization` | 1 |
| `node.begin` | 2 |
| `node.finish` | 2 |
| `node.mutation` | 1 |
| `node.policy_retrieval` | 1 |
| `node.read_tools` | 1 |
| `node.routing` | 2 |
| `retrieval.prepare` | 2 |
| `retrieval.rank` | 2 |
| `retrieval.search` | 2 |
| `tool.assign_lead` | 1 |
| `tool.get_lead_details` | 1 |
| `tool.retrieve_policy` | 1 |

All 4 local model spans contained available integer token usage; all outbound records contained timestamps and duration metadata. The audit inspected SDK-normalized records immediately before transport and retained their exact safe bodies. Inputs were empty; outputs contained only numeric usage; metadata contained only duration/status. No prompts, CRM records, approval bindings, consent signatures, request keys, exception text or environment snapshots were present. Credential values were checked in memory and not found in trace bodies. Normal SDK authentication still uses the API key in its HTTPS authentication header. TLS packets and unavailable hosted bodies were not inspected.

Evidence: [demonstration and readback status](final_validation_runs/20261026-7b84c120/langsmith_demo_live/langsmith_verification.json), [audited outbound records and all run IDs](final_validation_runs/20261026-7b84c120/langsmith_demo_live/langsmith_outbound_records.json), [direct run-read result](final_validation_runs/20261026-7b84c120/langsmith_demo_live/independent_read_run_check.json), [endpoint connectivity](final_validation_runs/20261026-7b84c120/langsmith_demo_live/endpoint_connectivity_check.json).

The raw demonstration status artifact's `hosted_latency_fields_present` field is vacuously true because its hosted list is empty. It must not be interpreted as verification; this report explicitly treats all hosted fields as unknown.

To finish hosted verification, use a valid LangSmith key with access to the configured workspace/project, confirm the account's regional API endpoint, and set `LANGSMITH_WORKSPACE_ID` if the key requires explicit workspace selection. Keep `SALES_COPILOT_LANGSMITH=true` and configure credentials privately in `.env` or the process environment. First retry SDK project lookup and direct retrieval of the recorded root IDs; a new paid Gemini demonstration is not needed merely to repeat a readback check.

## 2. Genuine Ragas evaluation

**Eight answers were generated and judging was attempted for all eight; six received at least one valid score, and four received all three.** The selection includes four straightforward questions (Q01, Q05, Q07, Q11) and four multi-policy questions (Q21, Q22, Q24, Q28). The existing hybrid retriever ran with actual cached MiniLM and cross-encoder models against the unchanged 31-question labelled dataset. The eight selected answers were generated by real Gemini calls using only the returned canonical passages. Raw answers, ordered context citations, timestamps, model names, response hashes and provider usage were saved without post-editing or citation repair.

The existing Ragas `judge` function was used with explicit opt-in, the existing Gemini judge factory and local MiniLM embeddings. Its optional dependencies were isolated under `.cache/ragas-sdk` and `.cache/ragas-compat`; the application environment was unchanged. No answer/reference ground truth was invented. Values below are rounded to six decimals; artifacts retain full precision.

| Question | Policy scope | Faithfulness | Answer relevancy | Context utilization | Label Recall@3 |
| --- | --- | ---: | ---: | ---: | ---: |
| Q01 | Territory | 1.000000 | 0.861402 | 1.000000 | 1.000 |
| Q05 | Certification | 1.000000 | 0.998394 | 1.000000 | 1.000 |
| Q07 | VIP | 1.000000 | 0.763927 | 1.000000 | 1.000 |
| Q11 | Capacity | 1.000000 | 0.968059 | 1.000000 | 1.000 |
| Q21 | Territory + priority | 0.875000 | 0.325338 | error | 0.500 |
| Q22 | Industry + certification | 1.000000 | 0.761700 | error | 1.000 |
| Q24 | Availability + capacity | error | error | error | 0.500 |
| Q28 | Ownership + management authority | error | error | error | 1.000 |

| Metric | Selected-sample mean | Measured | Skipped | Errors |
| --- | ---: | ---: | ---: | ---: |
| faithfulness | 0.979167 | 6 | 0 | 2 |
| response_relevancy | 0.779803 | 6 | 0 | 2 |
| context_utilization | 1.000000 | 4 | 0 | 4 |

The pipeline intentionally retains all 31 original dataset rows. The other **23 questions per metric were skipped because no generated response was supplied**; they are outside the selected evaluation sample. Full-pipeline measured/skipped/error counts are `faithfulness`: 6/23/2; `response_relevancy`: 6/23/2; `context_utilization`: 4/23/4. Means use measured values only.

Separately, deterministic retrieval on the eight selected queries achieved mean label Recall@3 **0.875** and MRR@5 **1.000**. These reuse the existing policy-ID labels and passage-position definitions. They are not LLM judgments. Q21 and Q24 each retrieved only one of two expected policies in the first three positions, so high faithfulness does not establish complete policy coverage.

The strict separate-bracket citation check passed **6/8** answers. Q24 and Q28 combined multiple valid IDs inside one bracket; the original output and the format failures are retained. No new citation or reference answer was fabricated to repair them. Canonical context references remain unchanged.

Evidence: [prepared dataset and contexts](final_validation_runs/20261026-7b84c120/prepared/samples.json), [retrieval rankings and labels](final_validation_runs/20261026-7b84c120/prepared/retrieval.json), [real Gemini responses](final_validation_runs/20261026-7b84c120/responses.json), [response provenance and citation checks](final_validation_runs/20261026-7b84c120/answer_provenance.json), [per-question Ragas results](final_validation_runs/20261026-7b84c120/judged/report.json), [judge execution and API usage](final_validation_runs/20261026-7b84c120/judge_execution.json).

## 3. Models, API usage and observed latency

Generator: `gemini-3.5-flash-lite`. Judge: `gemini-3.5-flash-lite`. Retrieval/embedding model: `sentence-transformers/all-MiniLM-L6-v2`. Reranker: `cross-encoder/ms-marco-MiniLM-L6-v2`. Ragas: `0.4.3`.

| Phase | Started adapter calls | Failed callbacks | Calls with usage | Input tokens | Output tokens | Total tokens | Mean model latency (ms) |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| Agent demo | 4 | 0 | 4 | 4475 | 436 | 4911 | 2295.386 |
| Answer generation | 8 | 0 | 8 | 3843 | 1384 | 5227 | 1191.637 |
| Ragas judge | 53 | 8 | 45 | 36926 | 3266 | 40192 | 3694.472 |

Observed total: **65 started Gemini adapter calls**, **45,244 input**, **5,086 output**, **50,330 total tokens**. These are adapter/provider observations, not independently reconciled billing records; no monetary cost is inferred. Requests were sequential within each phase and paced at least six seconds between starts. The application SDK's single-attempt setting was retained; no retry sweep was added. Per-call model latency excludes pacing; graph durations include the application's local execution and any pacing waits. Hosted latency remains unknown.

Provider/SDK failure ledger (raw exceptions and request bodies are not logged):

| Phase | Call | Typed operation | Exception | HTTP status | Category |
| --- | ---: | --- | --- | ---: | --- |
| Ragas judge | 39 | `ContextPrecisionOutput` | `GoogleAPIError` | 503 | `model_unavailable` |
| Ragas judge | 47 | `ContextPrecisionOutput` | `GoogleRateLimitError` | 429 | `model_rate_limited` |
| Ragas judge | 48 | `StatementGeneratorOutput` | `GoogleRateLimitError` | 429 | `model_rate_limited` |
| Ragas judge | 49 | `AnswerRelevanceOutput` | `GoogleRateLimitError` | 429 | `model_rate_limited` |
| Ragas judge | 50 | `ContextPrecisionOutput` | `GoogleRateLimitError` | 429 | `model_rate_limited` |
| Ragas judge | 51 | `StatementGeneratorOutput` | `GoogleRateLimitError` | 429 | `model_rate_limited` |
| Ragas judge | 52 | `AnswerRelevanceOutput` | `GoogleRateLimitError` | 429 | `model_rate_limited` |
| Ragas judge | 53 | `ContextPrecisionOutput` | `GoogleRateLimitError` | 429 | `model_rate_limited` |

Failed metrics remain null and are excluded from their means. No failed response was substituted or re-run to improve the reported score. An error in one metric does not erase completed metrics for that question.

The run encountered **one HTTP 503** and **seven HTTP 429** responses. The first 429 occurred at judge call 47; the existing pipeline attempted the remaining six metric operations at its six-second pacing and each also returned 429. The bounded run then ended. No further live calls or retry sweep was initiated. The retained error ledger does not establish the quota dimension (per-minute versus daily) or a provider retry-after duration.

| Package | Version in judge environment |
| --- | --- |
| `google-genai` | `2.33.0` |
| `instructor` | `1.17.0` |
| `langchain-community` | `0.4.1` |
| `langchain-core` | `1.7.0` |
| `langchain-google-genai` | `4.4.2` |
| `langgraph` | `1.2.17` |
| `langsmith` | `0.14.12` |
| `ragas` | `0.4.3` |
| `sentence-transformers` | `6.2.0` |
| `transformers` | `5.20.2` |

## 4. Limitations and regression checks

- LangSmith hosted delivery, hosted privacy and dashboard visibility remain unverified because readback is forbidden. Local span capture and a successful agent action do not establish hosted delivery.
- Eight deliberately selected questions form a small, nonrandom fictional sample. No repeated trials, confidence intervals, production CRM validation or broad reliability claim is supported.
- Rate-limit errors censored the later multi-policy questions. The four measured context-utilization scores are all straightforward questions; their near-1.0 average must not be generalized to the four multi-policy questions. Q24 and Q28 have no completed judge metrics. Report means with their actual denominators, never as eight fully scored samples.
- Generator and judge use the same Gemini model family, so their errors and preferences can be correlated. LLM-as-judge scores depend on prompts, decomposition, stochastic outputs and the supplied contexts; they are not human adjudication or ground-truth accuracy.
- Faithfulness measures support within retrieved text, not corpus completeness or correctness of policy authority. Answer relevancy is embedding-based after judge question generation and does not prove answer correctness. Context utilization is response-relative, so omitted evidence can be missed; the deterministic label recall exposes this.
- The authorization statement in generated Q28 prose must not be treated as permission. Existing backend eligibility and human-consent gates remain the only execution authority.
- Application LangGraph, assignment service, authorization and transaction code were not edited. The agent source fingerprint still matches the previously completed scripted benchmark. No historical artifact was overwritten and no new 30-scenario benchmark was initiated.
- Relevant isolated-environment tests: **27 passed in 29.47 seconds** using `python -m pytest tests/test_observability.py tests/test_rag_eval.py tests/test_ragas_sdk.py`. Only one-off validation drivers and new evidence/report files were added; runtime configuration changes were process-local.

## 5. Defensible resume bullet

- Evaluated eight Gemini-generated policy answers using Ragas and audited 30 metadata-only trace payloads without changing authorization or transactions; reported metric failures and left hosted LangSmith delivery explicitly unverified.

Machine-readable evidence index: [final summary and artifact hashes](final_validation_runs/20261026-7b84c120/final_summary.json).
