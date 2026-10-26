"""Record real SQLite/retrieval/graph behavior on disposable fictional databases.

The model and approval decisions are scripted test inputs, explicitly labelled.
No live Gemini call, existing CRM mutation or silent user approval occurs.
Run from the project root: python scripts/stage3_trace_demo.py
"""

import json
import os
import sqlite3
import tempfile
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from pathlib import Path
from threading import Barrier
from uuid import uuid4

os.environ.setdefault("HF_HUB_OFFLINE", "1")

from langchain_core.messages import AIMessage
from sales_copilot.assignment import AssignmentService
from sales_copilot.db import CRM
from sales_copilot.demo import create_demo_database
from sales_copilot.graph import Copilot
from sales_copilot.policy_gate import retrieve_for_action
from sales_copilot.retrieval import PolicyRetriever
from sales_copilot.tracing import Trace


def main():
    events = []
    def sink(line):
        events.append(json.loads(line.removeprefix("TRACE ")))
    def record(event, **facts):
        events.append({"event": event, **facts})
    trace = Trace(sink)
    retriever = PolicyRetriever(trace=trace)
    retriever.prepare()

    class ScriptedProposalModel:
        def with_structured_output(self, schema, **kwargs):
            from sales_copilot.agent_eval.fixtures import ScriptedIntentResponse
            return ScriptedIntentResponse(schema)

        def bind_tools(self, tools):
            return self
        def invoke(self, messages):
            return AIMessage(content="", tool_calls=[{"name": "assign_lead", "args": {
                "lead_id": 21, "assignee_id": 1, "request_key": "model-key-ignored"}, "id": "demo-call"}])

    def query(crm, sql, params=()):
        with closing(sqlite3.connect(crm.path)) as conn, conn:
            return conn.execute(sql, params).fetchall()
    def state(crm):
        return {"owner_id": query(crm, "SELECT owner_id FROM leads WHERE id=21")[0][0],
                "audit_count": query(crm, "SELECT count(*) FROM assignment_audit")[0][0],
                "completed_count": query(crm, "SELECT count(*) FROM assignment_operations")[0][0]}
    def args(lead=21):
        return {"lead_id": lead, "assignee_id": 1, "request_key": str(uuid4())}
    def approved(service, action):
        evidence = retrieve_for_action(service.crm, retriever, action)
        challenge, denied = service.propose(action, evidence)
        assert denied is None, denied
        return service.confirm(challenge, {"approved": True, **service.decision_context(challenge)})

    record("scope", model="scripted native-tool-call-shaped proposal", approval="explicit scripted test decisions",
           retrieval="real cached dense/BM25/RRF/cross-encoder models", database="temporary SQLite; existing CRM untouched")
    with tempfile.TemporaryDirectory(prefix="copilot-stage3-") as folder:
        counter = 0
        def fresh():
            nonlocal counter
            counter += 1
            return CRM(create_demo_database(Path(folder) / f"demo{counter}.sqlite3"))
        crm = fresh()
        bot = Copilot(crm, ScriptedProposalModel(), thread_id="stage3-trace", trace=trace,
                      actor_id="demo-dispatcher", policy_retriever=retriever)
        paused = bot.chat("Assign unassigned lead 21 to Chennai agent 1")
        assert paused.get("__interrupt__")
        record("before_approval", **state(crm))
        # Explicit TEST decision, not represented as an actual human approving a CRM write.
        record("scripted_decision", approved=True, actor_id="demo-dispatcher")
        result = bot.resume({"approved": True})
        record("after_approval", answer=result["answer"], **state(crm))
        for point in ("before_mutation", "after_lead_update", "after_audit", "after_commit"):
            crm = fresh()
            def fault(location):
                if location == point:
                    raise TimeoutError("Controlled test failure")
            service = AssignmentService(crm, retriever, actor_id="demo-dispatcher", trace=trace, fault=fault)
            ticket = approved(service, args())
            failure = service.execute(ticket)
            record("injected_failure", point=point, outcome=failure, **state(crm))
            service.fault = None
            record("same_operation_retry", point=point, outcome=service.execute(ticket), **state(crm))
        crm = fresh()
        query(crm, "UPDATE leads SET status='won' WHERE owner_id=1")
        for lead in range(1000, 1024):
            query(crm, "INSERT INTO leads SELECT ?,name,email,company,city,'new',score,priority,1,created_at,updated_at FROM leads WHERE id=21", (lead,))
        query(crm, "INSERT INTO leads SELECT 22,name,email,company,city,status,score,priority,NULL,created_at,updated_at FROM leads WHERE id=21")
        query(crm, "INSERT INTO lead_intake SELECT 22,industry,strategic,territory_confirmed,compliance_review_until,verified_at,source FROM lead_intake WHERE lead_id=21")
        one = AssignmentService(crm, retriever, actor_id="demo-dispatcher", trace=trace)
        two = AssignmentService(crm, retriever, actor_id="demo-dispatcher", trace=trace)
        pairs = [(one, approved(one, args())), (two, approved(two, args(22)))]
        barrier = Barrier(2)
        def execute(pair):
            barrier.wait(timeout=5)
            return pair[0].execute(pair[1])
        with ThreadPoolExecutor(2) as pool:
            outcomes = list(pool.map(execute, pairs))
        record("concurrent_last_capacity_slot", outcomes=outcomes,
               active_count=query(crm, "SELECT count(*) FROM leads WHERE owner_id=1 AND status IN ('new','contacted','qualified')")[0][0],
               audit_count=state(crm)["audit_count"])
    output = Path("docs/stage3_actual_trace.jsonl")
    output.write_text("\n".join(json.dumps(e, ensure_ascii=False) for e in events) + "\n", encoding="utf-8")
    print(f"Saved {len(events)} real execution events to {output}")
    for event in events:
        if "event" in event:
            print(json.dumps(event))


if __name__ == "__main__":
    main()
