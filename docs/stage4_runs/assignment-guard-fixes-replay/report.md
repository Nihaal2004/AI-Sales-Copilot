# Offline P03/F01/F02 adapter regressions

Gemini was NOT called. All models and retrieval are explicit local doubles; real graph, canonical citation validation and isolated SQLite fixtures are used. These are not new live success measurements.

The unchanged benchmark requests, fixtures, approvals, expectations and grader are used. The live adapter code path is exercised via injected local model factories. P03 uses saved native-tool shapes and a context-sensitive recovery proposal. F01/F02 use authored read-subject IDs to reproduce the schema inconsistency; the original raw intent JSON was not saved.

| Scenario | Outcome | All contracts |
| --- | --- | --- |
| P03 | {'status': 'policy_blocked', 'error': 'policy_citation_invalid'} | PASS |
| F01 | {'status': 'read_success', 'error': None} | PASS |
| F02 | {'status': 'read_success', 'error': None} | PASS |

Actual events: [trajectories](trajectories.jsonl). Complete contracts and SQLite checks: [JSON](results.json).

Result: 3/3. Provider API calls: 0. Token usage: null (unavailable).

Regression source: [tests](../../../tests/test_assignment_guard_live_failures.py). Run `.\.venv\Scripts\python.exe -m pytest -o addopts='' tests/test_assignment_guard_live_failures.py -q`.
