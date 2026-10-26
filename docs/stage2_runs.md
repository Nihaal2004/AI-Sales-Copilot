# Stage 2 recorded runs

All examples below are taken from actual execution artifacts. CRM write demonstrations used temporary seeded databases, not the existing CRM.

## Live Gemini policy query and denied assignment

Gemini selected the structured `retrieve_policy` tool for a priority-response query. Here are the first three results from each actual retrieval stage:

### bm25

| Citation | Score |
| --- | ---: |
| [CRM-PRI-009@1.0/S1/C1-c30ebe55da] | 13.371135 |
| [CRM-PRI-009@1.0/S3/C1-ef56827d6a] | 10.352172 |
| [CRM-PRI-009@1.0/S2/C1-2ba5a99bc3] | 8.097675 |

### dense

| Citation | Score |
| --- | ---: |
| [CRM-PRI-009@1.0/S1/C1-c30ebe55da] | 0.75873 |
| [CRM-PRI-009@1.0/S2/C1-2ba5a99bc3] | 0.625716 |
| [CRM-PRI-009@1.0/S6/C1-a079f62e50] | 0.617123 |

### fused

| Citation | Score |
| --- | ---: |
| [CRM-PRI-009@1.0/S1/C1-c30ebe55da] | 0.032787 |
| [CRM-PRI-009@1.0/S2/C1-2ba5a99bc3] | 0.032002 |
| [CRM-PRI-009@1.0/S3/C1-ef56827d6a] | 0.031754 |

### reranked

| Citation | Score |
| --- | ---: |
| [CRM-PRI-009@1.0/S1/C1-c30ebe55da] | 8.756104 |
| [CRM-PRI-009@1.0/S3/C1-ef56827d6a] | 6.747267 |
| [CRM-PRI-009@1.0/S2/C1-2ba5a99bc3] | 3.420575 |

The assignment candidate later passed action-specific retrieval and paused with five canonical references. The scripted resume decision in the live demo was `approved=False`. There was no mutation node in this live run. The temporary lead owner remained `NULL`.

Selected actual graph events:

```text
TRACE {"node": "policy_retrieval"}
TRACE {"node": "policy_retrieval", "result": {"ok": true, "citations": ["[CRM-OWN-010@1.0/S5/C1-d539144858]", "[CRM-OWN-010@1.0/S1/C1-428d7b1a4e]", "[CRM-TERR-001@1.0/S1/C1-28d6fb8d12]", "[CRM-REASSIGN-005@1.0/S1/C1-5d23bf86bd]", "[CRM-REASSIGN-005@1.0/S5/C1-dbcdea3787]"]}}
TRACE {"node": "approval", "tool": "assign_lead", "arguments": {"lead_id": 21, "assignee_id": 1, "request_key": "<redacted>"}}
TRACE {"node": "approval", "tool": "assign_lead", "arguments": {"lead_id": 21, "assignee_id": 1, "request_key": "<redacted>"}}
TRACE {"node": "approval", "result": {"ok": false, "error": "approval_denied"}}
```

## Approved temporary assignment with real retrieval

This run used a scripted native-tool candidate, real local dense/BM25/RRF/cross-encoder retrieval, the compiled StateGraph and a temporary SQLite CRM. Before resume the lead owner was `NULL`. After the explicit `approved=True` decision, SQLite confirmed owner 1.

```text
TRACE {"node": "approval", "tool": "assign_lead", "arguments": {"lead_id": 21, "assignee_id": 1, "request_key": "<redacted>"}}
TRACE {"node": "approval", "tool": "assign_lead", "arguments": {"lead_id": 21, "assignee_id": 1, "request_key": "<redacted>"}}
TRACE {"node": "approval", "result": {"approved": true}}
TRACE {"node": "mutation"}
TRACE {"node": "mutation", "tool": "assign_lead", "arguments": {"lead_id": 21, "assignee_id": 1, "request_key": "<redacted>"}, "result": {"ok": true, "lead_id": 21, "previous_owner_id": null, "owner_id": 1, "updated_at": "2026-10-25T12:16:18.348416+00:00", "request_key": "<redacted>"}}
```

The compact excerpts above omit lengthy rankings; the full source traces are [stage2_actual_trace.jsonl](stage2_actual_trace.jsonl) and [stage2_approved_trace.jsonl](stage2_approved_trace.jsonl).

## Verification

- Full suite: 96 passed in 4.78s; the original 67 Stage 1 tests remain.
- `pip check`: no broken requirements.
- Existing CRM lead-row fingerprint unchanged.
- Existing `.env` byte fingerprint unchanged.
- Evaluation source fingerprint matches the current 10-policy / 60-chunk corpus.
- Retrieval metrics are actual local-model measurements on 28 positive and three negative queries; see [stage2_evaluation.json](stage2_evaluation.json).
