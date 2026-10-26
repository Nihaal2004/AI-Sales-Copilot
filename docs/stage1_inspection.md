# Stage 1 database and response inspection

Inspected the actual local `data/crm.sqlite3` on 2026-10-25. This report contains SQLite results, not model-invented records.

## Database findings

Before the change: 20 leads, 4 agents, 7 Chennai leads, and **0 unassigned Chennai leads**. The live `owner_id` column was `INTEGER NOT NULL REFERENCES agents(id)`, preventing any null owner. The existing `unassigned_only` filter correctly used `owner_id IS NULL`; passing `owner_id=None` without that flag meant no owner filter.

After the authorized seed addition: 21 leads (Chennai 8, Bangalore 7, Hyderabad 6), 4 agents, and **1 unassigned Chennai lead**. Every field of each original lead and agent was verified unchanged. `PRAGMA foreign_key_check` returned no violations. The pre-change database is backed up as `data/crm.before_unassigned.sqlite3`.

## Actual leads grouped by city, status and owner_id

Each row is one SQL group. IDs 1 through 20 are the unchanged original records; ID 21 is the added fictional fixture. `NULL` means unassigned.

| City | Status | owner_id | Count | Actual leads |
| --- | --- | --- | --- | --- |
| Bangalore | contacted | 1 | 1 | 17: Fictional Lead 17 |
| Bangalore | contacted | 2 | 1 | 2: Fictional Lead 02 |
| Bangalore | lost | 1 | 1 | 5: Fictional Lead 05 |
| Bangalore | lost | 4 | 1 | 20: Fictional Lead 20 |
| Bangalore | new | 3 | 1 | 11: Fictional Lead 11 |
| Bangalore | qualified | 4 | 1 | 8: Fictional Lead 08 |
| Bangalore | won | 2 | 1 | 14: Fictional Lead 14 |
| Chennai | contacted | 3 | 1 | 7: Fictional Lead 07 |
| Chennai | lost | 2 | 1 | 10: Fictional Lead 10 |
| Chennai | new | NULL | 1 | 21: Kavya Iyer (Fictional) |
| Chennai | new | 1 | 1 | 1: Fictional Lead 01 |
| Chennai | new | 4 | 1 | 16: Fictional Lead 16 |
| Chennai | qualified | 1 | 1 | 13: Fictional Lead 13 |
| Chennai | won | 3 | 1 | 19: Fictional Lead 19 |
| Chennai | won | 4 | 1 | 4: Fictional Lead 04 |
| Hyderabad | contacted | 4 | 1 | 12: Fictional Lead 12 |
| Hyderabad | lost | 3 | 1 | 15: Fictional Lead 15 |
| Hyderabad | new | 2 | 1 | 6: Fictional Lead 06 |
| Hyderabad | qualified | 2 | 1 | 18: Fictional Lead 18 |
| Hyderabad | qualified | 3 | 1 | 3: Fictional Lead 03 |
| Hyderabad | won | 1 | 1 | 9: Fictional Lead 09 |

Agents: 1 = Anika Demo, 2 = Ravi Example, 3 = Meera Sample, 4 = Dev Fiction.

## Added record confirmed by SQLite

```json
{
  "id": 21,
  "name": "Kavya Iyer (Fictional)",
  "email": "kavya.iyer@marinaretail.example.test",
  "company": "Marina Retail Labs (Fictional)",
  "city": "Chennai",
  "status": "new",
  "score": 82,
  "priority": "high",
  "owner_id": null,
  "created_at": "2026-10-25T10:34:28.211776+00:00",
  "updated_at": "2026-10-25T10:34:28.211776+00:00"
}
```

The schema upgrade removes only the owner NOT NULL constraint, retaining the foreign key. The table rebuild, copied rows, recreated indexes/triggers and inserted fixture run in one transaction. A simulated failure verifies rollback of the schema and data. The seeder skips an existing matching lead and does not duplicate or unassign this fixture if it has subsequently been assigned.

Search AND conditions and null-owner semantics are unchanged. The old `LIMIT 20` was removed to return all 21 matches for an unfiltered request. Details now use `LEFT JOIN` so null-owner records are returned. No create/unassign agent tool was added.

## Why the CLI displayed JSON

Before the change, a recording wrapper around the real Gemini adapter observed:

1. An `AIMessage` with one native tool call.
2. An `AIMessage` with zero tool calls and conversational final text.

The actual final model text was:

> There are no unassigned leads from Chennai in the system (all leads currently have an assigned owner). 
> 
> If you'd like, I can show you all leads from Chennai regardless of whether they are assigned, or search for unassigned leads in other cities. Let me know how you'd like to proceed!

The graph nevertheless returned this answer:

```text
search_leads: {"ok": true, "leads": []}
```

Root cause: `graph.py:finish` intentionally ignored model prose and called `json.dumps` on verified tool results. `cli.py` only printed `result["answer"]`; it did not fall back to rendering ToolMessages. It was not a Gemini content-block parsing failure.

The current `presentation.py` renders verified data conversationally. The original final model response is saved separately in `state["final_ai_message"]` and is never mistaken for application-generated or error AI messages. `answer_source` and finish traces reveal whether a genuine final model message arrived. This preserves the rule that model text cannot fabricate assignment success. Failed model/tool requests remain visible with safe error codes and HTTP status where available.

## Live verification after the change

Model final type: `AIMessage`. Answer source: `verified_results`. The live read completed in 2.7 seconds.

The actual model final text:

> Here is the unassigned lead from Chennai:
> 
> * **Name:** Kavya Iyer (Fictional)
> * **ID:** 21
> * **Company:** Marina Retail Labs (Fictional)
> * **Email:** kavya.iyer@marinaretail.example.test
> * **Status:** New
> * **Score:** 82 (High priority)

The displayed verified answer:

```text
I found 1 unassigned lead in Chennai:
- Lead 21: Kavya Iyer (Fictional) at Marina Retail Labs (Fictional); Chennai; status new; score 82; high priority; unassigned.
```

Selected actual trace lines:

```text
TRACE {"node": "agent", "result": {"message_type": "AIMessage", "response_kind": "tool_calls", "has_text": false}}
TRACE {"node": "agent", "result": {"message_type": "AIMessage", "response_kind": "final", "has_text": true}}
TRACE {"node": "finish", "result": {"answer_source": "verified_results", "model_final_received": true, "model_final_has_text": true}}
```

## Regression verification

`python -m pytest`: **67 passed in 6.95s**. Includes old-schema upgrade and rollback, unchanged original rows and preserved indexes/triggers, real null-owner search/details, all 21 records, AND filters, reseeding after approval, string and Gemini-style content-block final messages, empty final messages, readable CLI answers, visible provider errors and fabricated-success rejection. The live Gemini read was checked separately from the offline suite.
