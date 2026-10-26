# Stage 2 retrieval error analysis

Audit date: 2026-10-25. Scope: the existing saved evaluation and current policy sources. This is an analysis-only change; production code, corpus, labels, saved evaluation, CRM and configuration were not modified. No model inference or metric regeneration command was run.

## Recommendation

**Do not retain cross-encoder ordering as the unconditional default on the present evidence. Prefer RRF ordering for a follow-up default, with cross-encoder reranking retained as an opt-in experiment until independently adjudicated, held-out evaluation demonstrates a benefit.** This is a recommendation, not an implemented switch; current production code still reranks unconditionally.

The official labels show exactly three regressions and no improvements versus RRF: Q20 loses reciprocal rank, and Q21/Q23 lose policy coverage in the first three passages. However, Q20 has a clear unlabelled-but-relevant result, and Q23 exposes overlapping content across policy documents. These are evaluation limitations, not grounds for claiming the cross-encoder is generally worse. The defensible conclusion is that this development set does not justify unconditional reranking.

**Do not simply delete cross-encoder scoring.** Current `applicable()` and citation validation require finite cross scores and apply a cross-logit relevance threshold. A future change should separate ordering from relevance/abstention checks, preserve citation grounding, action binding, mandatory retrieval and explicit approval, and validate the resulting delivered passages. Keeping cross scores for gating while using RRF ordering would still incur cross-encoder compute; no latency saving has been measured here.

## Sources and validation scope

- [Saved evaluation](stage2_evaluation.json), [static labels](../evaluation/policy_queries.json), [metric implementation](../sales_copilot/evaluation.py), [retrieval implementation](../sales_copilot/retrieval.py), and the ten [policy documents](../sales_copilot/policies/manifest.json).
- Saved evaluation SHA-256: `78c32a0304ebc9be7ab02369e48f1520ea5e763edeee3a3eb9e39d86100f052c`.
- Corpus fingerprint: `64a7f402e0be29c2c4b389fd1e0bc9d5f42a57ea31af9ade702e34b42f629c7e`; it matches the current canonical corpus.
- Dataset: 28 positive queries, including 20 single-policy and 8 two-policy queries, plus 3 unrelated negatives.
- Independently recomputed metrics directly from saved rankings using exact rational arithmetic, without calling the production `query_metrics()` function. All 224 positive per-query metric values and all 24 negative null values match. All four aggregate metric pairs match within 1e-12.
- All 620 saved references resolve to canonical chunks. Saved list ordering is consistent with the applicable score and stable-ID tie-breaker; repeated query/chunk score records agree across lists. The label/query snapshot in the artifact exactly matches the current label file.
- The artifact stores only five entries per method, although retrieval considers up to 12 dense and 12 lexical candidates, fuses their union, and reranks the best 12 fused candidates. Therefore this audit can validate Recall@3/MRR@5 exactly, but cannot reconstruct every original RRF contribution, exact ranks beyond five, full candidate recall, or a complete unique-policy top-five ranking. No claim is made to have rerun or independently reproduced the neural logits.

## Metric definitions and independent aggregation

For positive query q, let G(q) be the set of labelled policy IDs and C(q) be the ordered retrieved chunks:

```text
Recall@3(q) = |G(q) intersection {policy_id(c) for c in C(q)[0:3]}| / |G(q)|
RR@5(q)     = 1 / first relevant chunk position, if one exists in positions 1..5; otherwise 0
Reported Recall@3 = sum of query Recall@3 values / 28
Reported MRR@5    = sum of query RR@5 values / 28
```

These are macro averages: each query has equal weight, regardless of whether it has one or two expected policies. Multiple chunks from the same policy occupy separate retrieval positions but contribute that policy only once to recall. A two-policy query with just one represented policy gets recall 0.5. MRR checks only the first relevant chunk; it does not reward covering the second policy. Negative queries get null metrics and are excluded from both denominators.

| Method | Sum of query recalls | Sum of query RR | Recall@3 | MRR@5 | Micro policy-hit recall (diagnostic only) |
| --- | ---: | ---: | ---: | ---: | ---: |
| BM25 | 26 | 136/5 | 0.928571429 | 0.971428571 | 11/12 = 0.916666667 |
| Dense | 55/2 | 27 | 0.982142857 | 0.964285714 | 35/36 = 0.972222222 |
| RRF | 55/2 | 28 | 0.982142857 | 1.000000000 | 35/36 = 0.972222222 |
| RRF + cross-encoder | 53/2 | 55/2 | 0.946428571 | 0.982142857 | 11/12 = 0.916666667 |

The reported numbers are arithmetically correct for these definitions. Micro recall uses 36 expected policy/query pairs and is not the reported macro metric. RRF to reranked changes are Recall@3 **-1/28 = -0.035714286** (3.571429 percentage points) and MRR@5 **-1/56 = -0.017857143** (1.785714 percentage points).

RRF misses one of Q24's two expected policies in the first three passages; this accounts for its recall sum of 27.5 rather than 28. Reranking preserves that miss and adds the Q21/Q23 losses, reducing the sum to 26.5. RRF has a relevant policy at rank 1 on every positive query; reranking changes only Q20 to first relevant rank 2, reducing the RR sum from 28 to 27.5. No query improves either metric against RRF; the other 25 positive queries are unchanged.

All three negatives have empty saved `applicable_citations`, consistent with the reported 3/3 abstentions. This is not evidence of a reliable production false-positive rate: the negatives concern cake, Neptune and Kubernetes, rather than difficult near-domain policy questions. The top-five raw rankings alone cannot re-establish threshold outcomes over every one of the 12 reranked candidates, although the saved abstention field and ranking code are consistent.

## Every regression under the existing labels

| Query | Recall@3: RRF → reranked | RR@5: RRF → reranked | Main issue |
| --- | ---: | ---: | --- |
| Q20 | 1 → 1 (delta 0) | 1 → 0.5 (delta -0.5) | Relevant unlabelled policy promoted; direct labelled passage demoted |
| Q21 | 1 → 0.5 (delta -0.5) | 1 → 1 (delta 0) | Three geographic chunks crowd out the priority policy |
| Q23 | 1 → 0.5 (delta -0.5) | 1 → 1 (delta 0) | Three VIP chunks crowd out the reassignment policy |

Below, repeated policy IDs are intentional: these are five chunk positions, not five distinct policies. Position `6–12` means outside the saved top five but inside the reranked candidate set; its exact position is not recoverable from the artifact. RRF scores and cross-encoder logits use different scales and must not be subtracted from each other. The score fields for a given query/chunk do not change between lists; reranking changes the ordering criterion.

## Q20

**Query:** If the proposed receiving agent changes, can we reuse the old policy evidence and approval?

**Ground-truth relevant policy IDs:** `CRM-OWN-010`.

**RRF top-5 policy IDs, in chunk order:**

`CRM-OWN-010 → CRM-REASSIGN-005 → CRM-OWN-010 → CRM-CERT-003 → CRM-MGMT-008`

**Reranked top-5 policy IDs, in chunk order:**

`CRM-REASSIGN-005 → CRM-OWN-010 → CRM-PRI-009 → CRM-OWN-010 → CRM-MGMT-008`

**Metric changes:** Recall@3 1.0 → 1.0 (+0.0); RR@5 1.0 → 0.5 (-0.5).

| Exact chunk citation | Section title | Labelled policy? | RRF position | Reranked position | RRF score | Cross logit |
| --- | --- | --- | ---: | ---: | ---: | ---: |
| `[CRM-OWN-010@1.0/S4/C1-097ca36b6a]` | Action-specific evidence binding | yes | 1 | 4 | 0.031280547 | -1.468771 |
| `[CRM-REASSIGN-005@1.0/S6/C1-f4b37a53a2]` | Handover examples and request keys | no | 2 | 1 | 0.031257631 | 3.156750 |
| `[CRM-OWN-010@1.0/S5/C1-d539144858]` | Assignment confirmation example | yes | 3 | 2 | 0.031009615 | 2.174314 |
| `[CRM-CERT-003@1.0/S6/C1-39b3a76c26]` | Register corrections and review trail | no | 4 | 6–12 | 0.030477950 | -4.843756 |
| `[CRM-MGMT-008@1.0/S1/C1-f0024bdd73]` | Documented management review | no | 5 | 5 | 0.030414747 | -1.665356 |
| `[CRM-PRI-009@1.0/S6/C1-a079f62e50]` | Priority review notes | no | 6–12 | 3 | 0.014925373 | -0.789578 |

### Passage-grounded interpretation

The original RRF leader, ownership S4, explicitly says:

> If any proposed action field changes, discard the previous binding and retrieve again.

The promoted reassignment S6 says:

> If the proposed receiving agent changes, the retrieval evidence must be rebound to the changed action and the human must review that exact new proposal.

Both directly answer the question. The latter has an exact six-token overlap with the query, “if the proposed receiving agent changes”, and a cross score of **3.156750**, compared with **-1.468771** for ownership S4 (gap **4.625521**). A plausible explanation is that the pointwise model strongly favors the explicit receiving-agent-change wording in the example. This is not evidence that the first answer became semantically irrelevant: `CRM-REASSIGN-005` is simply missing from Q20's ground truth.

Ownership S5, promoted from RRF rank 3 to reranked rank 2 with cross score **2.174314**, discusses confirmation for an initial assignment. It counts as relevant because it belongs to `CRM-OWN-010`, even though it is less direct about changed-action invalidation than reassignment S6. This simultaneously exposes an omitted relevant policy and coarse relevance within a labelled policy.

The exact binding passage moves from rank 1 to rank 4. In addition, its negative cross score falls below the production relevance threshold of 0.0, so the saved delivered evidence excludes it. The official RRF baseline is unfiltered, so its rank-1 metric should not be confused with what a hypothetical RRF-plus-existing-cross-filter runtime would deliver.

**Label sensitivity, not a revised official result:** if an independent reviewer adds `CRM-REASSIGN-005` alongside `CRM-OWN-010` for this query, both methods' Q20 RR become 1. Their recall stays 1, and the aggregate reranked MRR becomes **1.000000**, equal to RRF. This removes the entire reported MRR difference. The label file was not changed.

## Q21

**Query:** An urgent Chennai lead may move to a Bangalore agent. Explain geographic routing and response deadlines.

**Ground-truth relevant policy IDs:** `CRM-TERR-001`, `CRM-PRI-009`.

**RRF top-5 policy IDs, in chunk order:**

`CRM-TERR-001 → CRM-PRI-009 → CRM-TERR-001 → CRM-TERR-001 → CRM-TERR-001`

**Reranked top-5 policy IDs, in chunk order:**

`CRM-TERR-001 → CRM-TERR-001 → CRM-TERR-001 → CRM-PRI-009 → CRM-AVAIL-007`

**Metric changes:** Recall@3 1.0 → 0.5 (-0.5); RR@5 1.0 → 1.0 (+0.0).

| Exact chunk citation | Section title | Labelled policy? | RRF position | Reranked position | RRF score | Cross logit |
| --- | --- | --- | ---: | ---: | ---: | ---: |
| `[CRM-TERR-001@1.0/S1/C1-28d6fb8d12]` | City territory routing | yes | 1 | 2 | 0.032258065 | 2.508420 |
| `[CRM-PRI-009@1.0/S5/C1-ceea5b06fc]` | Urgent lead coverage example | yes | 2 | 4 | 0.032018443 | 1.998970 |
| `[CRM-TERR-001@1.0/S6/C1-da23baa335]` | Boundary cases and escalation | yes | 3 | 1 | 0.032018443 | 3.090022 |
| `[CRM-TERR-001@1.0/S4/C1-59a3f7189e]` | Cross-city assignment example | yes | 4 | 3 | 0.031746032 | 2.367741 |
| `[CRM-TERR-001@1.0/S3/C1-cb7702ef29]` | Primary buying office evidence | yes | 5 | 6–12 | 0.029850746 | -3.699665 |
| `[CRM-AVAIL-007@1.0/S5/C1-3a86df8ad2]` | Unavailable agent example | no | 6–12 | 5 | 0.029644269 | 0.902294 |

### Passage-grounded interpretation

Priority S5, RRF rank 2, gives a concrete response deadline:

> The two-business-hour target creates a reason to ask for a coverage plan.

The reranked top three are territory S6, S1 and S4. S1 identifies Chennai/Bangalore city routing. S4 describes almost the exact cross-city assignment scenario and says a short response target does not supply an exception. S6 connects urgent coverage to geography:

> If an urgent coverage instruction appears to conflict with geographic restrictions, show both the coverage and territory passages to the human reviewer.

These geographic passages are individually relevant, but none of the top three supplies the two-business-hour deadline. The cross-encoder puts territory S4 at **2.367741**, above priority S5 at **1.998970** (gap **0.368772**), while territory S6 leads at **3.090022**. Priority S5 moves from rank 2 to rank 4. All these passages exceed the relevance thresholds; the loss is caused by ordering and the three-passage budget, not a threshold rejection or missing candidate.

A plausible explanation is pointwise concentration on the city-routing aspect of a two-facet question. The scoring function evaluates each query/passage pair independently and does not penalize three passages that repeat one policy. This is a substantive top-three coverage loss under the stated geography-plus-deadline question, although MRR stays 1 because geography alone already supplies a labelled result at rank 1. Availability S5 enters rank 5, but it does not restore the missing priority policy in the first three.

## Q23

**Query:** A VIP prospect already has an owner. What sponsor review and handover evidence should accompany the transfer?

**Ground-truth relevant policy IDs:** `CRM-VIP-004`, `CRM-REASSIGN-005`.

**RRF top-5 policy IDs, in chunk order:**

`CRM-VIP-004 → CRM-VIP-004 → CRM-REASSIGN-005 → CRM-REASSIGN-005 → CRM-VIP-004`

**Reranked top-5 policy IDs, in chunk order:**

`CRM-VIP-004 → CRM-VIP-004 → CRM-VIP-004 → CRM-REASSIGN-005 → CRM-REASSIGN-005`

**Metric changes:** Recall@3 1.0 → 0.5 (-0.5); RR@5 1.0 → 1.0 (+0.0).

| Exact chunk citation | Section title | Labelled policy? | RRF position | Reranked position | RRF score | Cross logit |
| --- | --- | --- | ---: | ---: | ---: | ---: |
| `[CRM-VIP-004@1.0/S2/C1-10f26fbfee]` | VIP review scope | yes | 1 | 1 | 0.032522475 | 4.733198 |
| `[CRM-VIP-004@1.0/S4/C1-c53264522e]` | Sponsor handover requirements | yes | 2 | 2 | 0.032522475 | 4.491863 |
| `[CRM-REASSIGN-005@1.0/S5/C1-dbcdea3787]` | First assignment versus transfer | yes | 3 | 4 | 0.031257631 | 0.675037 |
| `[CRM-REASSIGN-005@1.0/S2/C1-f096951470]` | Handover exceptions | yes | 4 | 6–12 | 0.031024531 | 0.094646 |
| `[CRM-VIP-004@1.0/S6/C1-d21a24a939]` | VIP escalation packet | yes | 5 | 3 | 0.030536131 | 1.294350 |
| `[CRM-REASSIGN-005@1.0/S4/C1-be45e6dc0a]` | Ownership dispute procedure | yes | 6–12 | 5 | 0.015625000 | 0.674663 |

### Passage-grounded interpretation

RRF's first two passages are VIP S2/S4 and its third is reassignment S5. Reranking moves VIP S6 from fifth to third, leaving all three top positions under the VIP policy and pushing reassignment S5 to fourth.

VIP S4 already provides handover-related information:

> Before moving a VIP lead, gather the existing sponsor, current owner, proposed owner, live negotiation milestones and commitments made to the buyer.

VIP S6 adds a review packet and explicitly asks for other policies when relevant:

> Include relevant territorial, certification and reassignment passages when they affect the discussion.

VIP S6 scores **1.294350**, above reassignment S5 at **0.675037** (gap **0.619312**). The first two VIP passages score **4.733198** and **4.491863**. A plausible explanation is concentration on the explicit VIP/sponsor terminology and highly contextual review text. The model is not optimizing distinct-policy coverage, so another VIP passage displaces a second required policy. This is not a candidate-generation failure: reassignment S5 remains at rank 4 and above the relevance thresholds.

There is an important qualification. VIP S4 itself covers sponsor and handover facts, while the RRF-selected reassignment S5 mainly explains initial assignment versus transfer. The more explicit current-owner acknowledgement / receiving-agent acceptance requirement is in reassignment S1; it appears in the saved BM25 top five, but neither method's first three passages. Thus the policy-level recall loss is real under the supplied labels, but it is not by itself proof of a worse final answer. Passage-level judgments should establish whether a separate reassignment citation is essential, whether VIP S4 sufficiently answers the handover facet, and which reassignment section is actually useful.

## Are policy labels being compared with chunks correctly?

**The implementation is internally consistent, but its unit needs a precise name.** It compares each chunk's `policy_id` with the relevant policy-ID set. It does not accidentally compare policy IDs with chunk IDs. The reported recall is best described as **policy-ID coverage within a three-passage budget**, and MRR as **first labelled-policy passage within five passage positions**. These are useful context-budget proxies. They are not strict chunk-relevance metrics, because there are no chunk-level relevance labels, and they are not unique-policy Recall@3/MRR@5, because the cutoff is applied before policy deduplication.

Consequences:

1. Any section in a labelled policy gets full relevance credit, even when it does not answer the question. Q20 ownership S5 illustrates this.
2. An excellent answer in an unlabelled policy is treated as irrelevant. Q20 reassignment S6 illustrates this.
3. Similar passages from one policy consume several slots and can hide another required policy. Q21/Q23 illustrate this.
4. MRR cannot measure complete multi-policy review: Q21/Q23 retain MRR 1 while losing half the expected policy IDs.
5. Macro averaging gives each two-policy question the same weight as a single-policy question. Twenty easy single-policy queries dominate the 28-query average. On the eight multi-policy queries, RRF Recall@3 is **7.5/8 = 0.9375**, versus **6.5/8 = 0.8125** after reranking. On the twenty single-policy queries both have recall 1; the only RR difference is Q20.

If the intended unit is a policy document, first create a unique-policy ranking (for example by each policy's best chunk or first occurrence), then take the first 3/5 policies. If the intended unit is an evidence passage, label actual sections/chunks and report passage relevance plus aspect coverage. Reporting both is preferable; silently renaming one as the other is not.

For Q21, deduplicating only within the saved reranked top five gives TERR → PRI → AVAIL. For Q23 it gives VIP → REASSIGN. Both expected policy sets are then present. This diagnostic explains the diversity effect; it is **not** a recomputed complete unique-policy benchmark. Full/deeper rankings are needed when fewer unique policies appear in the saved prefix, or when another relevant policy might occur below position 5. No production deduplication was added.

## Evaluation leakage audit

### Direct label leakage: none found in the inspected application path

`evaluate()` passes only `query["query"]` to `retriever.rank()`. It uses `expected_policy_ids` afterward to grade the rankings. Dense encoding, BM25, RRF and cross-encoder scoring receive query text and policy search text, not relevance labels. The retrieval module does not load the query dataset. The corpus loader indexes the policy directory rather than the evaluation file, and its manifest requires the ten policy IDs. No app-level fine-tuning or label-dependent reranking code was found. The model loader uses the named public checkpoints. The aggregate metrics were recomputed rather than accepted from a mocked backend.

The same labels are used to score every method, and the current label file matches the saved snapshot. No method-specific label substitution or duplicate query ID was found. Including policy/section titles in indexed text is normal source metadata, not ground-truth leakage. This inspection cannot audit external pretrained-model training data or prove historical experiment independence from a working tree.

### Development-set and annotation contamination risks: present

- The documents and queries were co-developed in this mini-project, and the walkthrough calls this an author-labelled development set. There is no independent annotator, adjudication record, held-out split or frozen release protocol. These are development measurements, not out-of-sample estimates.
- The expanded policies contain examples close to the queries, implementation-specific wording and discussion of the reranker/approval machinery. That can make matching easier than operational questions. A lowercase-alphanumeric contiguous-token audit found shared spans of at least four tokens in **5/28** positives and at least five in **1/28**. Q20's six-token span is in the unlabelled reassignment passage. No entire positive question appeared as a contiguous token sequence. Wording overlap alone neither proves leakage nor establishes independence; paraphrased authoring overlap is not captured by this check.
- The qrels are incomplete/coarse, with direct evidence in Q20 and overlap concerns in Q23. High scores can reflect matching a policy family rather than retrieving the section needed to answer the question.
- The walkthrough says relevance thresholds were chosen before evaluation, but the artifact has no pre-registration, held-out threshold-selection set or experiment history to independently establish that claim. This audit found no code that tunes thresholds from the labels; it does not infer a historical tuning procedure that was not recorded.
- Now that this set has informed a default recommendation, it must be treated as development data for subsequent changes. Tuning reranking, diversity or thresholds and then reporting improvement on the same 28 questions would introduce further selection bias. Use a new frozen, independently adjudicated test set for the next comparison.
- The three distant-domain negatives are insufficient for threshold calibration or assignment-workflow safety assessment. Add difficult near-domain nonmatches, missing-policy questions, adversarial wording and partially answerable multi-policy questions.
- Public checkpoint IDs and package versions are recorded, but immutable model revisions, query-file hash, code revision and confidence intervals are not in the original evaluation artifact. The corpus fingerprint does match, which rules out a current source-version mismatch for this audit. No statistically significant general advantage/disadvantage is established by these three changed queries.

## Raw ranking metrics versus delivered evidence

The evaluator grades **unfiltered** ranking lists. The production retriever separately requires dense cosine >=0.25 and cross logit >=0.0, then takes the first `top_k` accepted passages. Saved `applicable_citations` reflect that filtered reranked output. Q20 has only two accepted passages and omits ownership S4 despite its official RRF rank 1. Q21/Q23 deliver three chunks from one policy; their second expected policy is outside the three-passage output.

Consequently, the quality table validates ordering, not end-to-end filtered-evidence coverage, rejection rate on positive queries, answer correctness or authorization. The assignment path retrieves up to five policy passages and applies an additional topical/action-binding gate, so a top-three policy-query regression is not automatically a demonstrated assignment failure. Future evaluation should separately report raw ranking, filtered top-3 policy answers, filtered top-5 assignment evidence, positive-query abstentions, negative false accepts, citation accuracy and multi-policy completeness.

The four quality lists are generated by one `rank()` invocation that always computes cross scores. The labels “BM25 only” and “dense only” describe which ranking is graded, not separate latency/cost experiments. The saved artifact cannot establish performance savings from disabling reranking.

## Recommended next experiment, without changing the current implementation

1. Independently adjudicate Q20 and review Q23 with section-level evidence; keep official and revised-label sensitivity results separate. Add graded passage/aspect qrels and several relevant sections per question where justified.
2. Freeze a new held-out set with realistic paraphrases, multi-policy requirements, near-domain negatives and known conflicts. Keep threshold selection and model-selection data separate from that test set.
3. Compare RRF ordering, cross-encoder ordering, and a diversity-aware variant under the same candidate pool, passage/token budget and relevance gates. Do not assume a diversity change is an improvement until measured.
4. Retain cross-scoring for relevance checks if needed while testing RRF ordering, or separately validate an alternative abstention mechanism before removing cross-scoring. Preserve action-specific retrieval, canonical citation validation and the human approval interrupt in all variants.
5. Save all dense/BM25/fused candidates, cross scores, delivered evidence, qrels/version hashes, immutable model revisions and timing. Report paired per-query changes and uncertainty rather than relying only on four aggregate averages.

**Decision for now:** RRF is the preferable proposed ordering default for this prototype because it has no measured losses and better labelled aggregate coverage than unconditional reranking on this development set. Keep reranking experimental, and qualify this decision with the Q20 label issue and the need for held-out evaluation. No runtime default, production code, label, policy or saved score has been changed by this report.

## Complete per-positive-query cross-check

Each entry is `Recall@3 / RR@5` under the original labels. All queries with either metric worsening are covered above.

| Query | RRF | Reranked | Changed? |
| --- | ---: | ---: | --- |
| Q01 | 1 / 1 | 1 / 1 | no |
| Q02 | 1 / 1 | 1 / 1 | no |
| Q03 | 1 / 1 | 1 / 1 | no |
| Q04 | 1 / 1 | 1 / 1 | no |
| Q05 | 1 / 1 | 1 / 1 | no |
| Q06 | 1 / 1 | 1 / 1 | no |
| Q07 | 1 / 1 | 1 / 1 | no |
| Q08 | 1 / 1 | 1 / 1 | no |
| Q09 | 1 / 1 | 1 / 1 | no |
| Q10 | 1 / 1 | 1 / 1 | no |
| Q11 | 1 / 1 | 1 / 1 | no |
| Q12 | 1 / 1 | 1 / 1 | no |
| Q13 | 1 / 1 | 1 / 1 | no |
| Q14 | 1 / 1 | 1 / 1 | no |
| Q15 | 1 / 1 | 1 / 1 | no |
| Q16 | 1 / 1 | 1 / 1 | no |
| Q17 | 1 / 1 | 1 / 1 | no |
| Q18 | 1 / 1 | 1 / 1 | no |
| Q19 | 1 / 1 | 1 / 1 | no |
| Q20 | 1 / 1 | 1 / 0.5 | yes |
| Q21 | 1 / 1 | 0.5 / 1 | yes |
| Q22 | 1 / 1 | 1 / 1 | no |
| Q23 | 1 / 1 | 0.5 / 1 | yes |
| Q24 | 0.5 / 1 | 0.5 / 1 | no |
| Q25 | 1 / 1 | 1 / 1 | no |
| Q26 | 1 / 1 | 1 / 1 | no |
| Q27 | 1 / 1 | 1 / 1 | no |
| Q28 | 1 / 1 | 1 / 1 | no |

Validation used read-only source/artifact inspection and independent numerical assertions. Production tests and neural inference were not rerun because this task changed only this analysis document.
