"""Minimal local CLI with an explicit, fail-closed approval prompt."""

import argparse
import json
import os
from uuid import uuid4

from dotenv import load_dotenv

from .db import CRM, seed_database
from .graph import Copilot
from .model import create_model
from .schemas import SearchLeads
from .corpus import PolicyError
from .retrieval import NOTICE, get_default_retriever
from .presentation import render_results
from .migrations import migrate_database, setup_demo_identity
from .demo import create_demo_database
from .assignment import AssignmentService
from .schemas import AssignLead
from .ranking import RankingError, load_ranking_config


def approval_decision(text: str) -> dict:
    # Only the exact word 'approve' grants approval. All other input denies it.
    return {"approved": text.strip().lower() == "approve"}


def main():
    load_dotenv(override=False)
    parser = argparse.ArgumentParser(description="AI Sales Copilot — Stage 3 first assignment")
    parser.add_argument("command", choices=["seed", "migrate", "setup-demo-identity", "demo-db", "result",
                                          "chat", "prepare-policies", "policy", "evaluate", "agent-evaluate"])
    parser.add_argument("--db", default=os.getenv("CRM_DB_PATH", "data/crm.sqlite3"))
    parser.add_argument("--thread-id", default=None)
    parser.add_argument("--query", help="Policy retrieval diagnostic query")
    parser.add_argument("--output", default="docs/stage2_evaluation.json", help="Evaluation artifact path")
    parser.add_argument("--demo-actor", action="store_true", help="Use explicitly provisioned SIMULATED demo-dispatcher identity")
    parser.add_argument("--operation-id", help="Original operation UUID: result lookup, or first chat intent after a restart")
    parser.add_argument("--lead-id", type=int)
    parser.add_argument("--assignee-id", type=int)
    parser.add_argument("--ranking-config", help="Trusted JSON weights for business ranking during chat; does not modify CRM data")
    parser.add_argument("--live-gemini", action="store_true", help="OPT IN to real Gemini API calls on isolated evaluation fixtures")
    parser.add_argument("--real-retrieval", action="store_true", help="Use actual cached hybrid models in scripted agent evaluation")
    parser.add_argument("--trials", type=int, default=1, help="Evaluation trials per scenario (1..20)")
    parser.add_argument("--scenario", action="append", help="Evaluation scenario ID; repeat to select multiple")
    parser.add_argument("--eval-output-dir", default="docs/stage4_runs")
    parser.add_argument("--eval-run-id", default=None)
    parser.add_argument("--resume-from", help="Parent evaluation directory; creates a NEW run and retries incomplete trials")
    parser.add_argument("--call-interval", type=float, default=4.0, help="Minimum seconds between live provider attempts")
    parser.add_argument("--batch-size", type=int, default=5, help="Scenario-trials between live batch pauses")
    parser.add_argument("--batch-pause", type=float, default=30.0)
    parser.add_argument("--max-attempts", type=int, default=3, help="Total provider attempts per logical LLM call")
    parser.add_argument("--backoff-base", type=float, default=2.0)
    parser.add_argument("--backoff-cap", type=float, default=30.0)
    parser.add_argument("--max-retry-wait", type=float, default=120.0, help="Cumulative retry sleep budget per LLM call")
    args = parser.parse_args()
    if args.ranking_config and args.command != "chat":
        parser.error("--ranking-config is supported for chat only")
    if args.command == "agent-evaluate":
        from .agent_eval.runner import run_evaluation
        from .agent_eval.quota import QuotaConfig
        print("Live Gemini opt-in: API calls may incur cost/quota." if args.live_gemini
              else "Scripted evaluation: no Gemini calls; this does not measure model reliability.")
        print("Isolated fictional databases only. Approval decisions are explicitly scripted fixture inputs.")
        try:
            report, target = run_evaluation(live=args.live_gemini, real_retrieval=args.real_retrieval,
                                           trials=args.trials, scenario_ids=args.scenario,
                                           output_dir=args.eval_output_dir, run_id=args.eval_run_id,
                                           resume_from=args.resume_from,
                                           quota_config=QuotaConfig(call_interval=args.call_interval, batch_size=args.batch_size,
                                                                    batch_pause=args.batch_pause, max_attempts=args.max_attempts,
                                                                    backoff_base=args.backoff_base, backoff_cap=args.backoff_cap,
                                                                    max_retry_wait=args.max_retry_wait))
        except (ValueError, FileExistsError) as exc:
            parser.error(str(exc))
        print(json.dumps(report["summary"], indent=2))
        print("Results and trajectories: " + str(target))
        return
    if args.command in {"prepare-policies", "policy", "evaluate"}:
        retriever = get_default_retriever()
        try:
            if args.command == "prepare-policies":
                retriever.prepare()
                print(f"Prepared {len(retriever.corpus.documents)} policies / {len(retriever.corpus.chunks)} chunks. "
                      f"Embedding cache hit: {retriever.cache_hit}.")
            elif args.command == "policy":
                if not args.query:
                    parser.error("policy requires --query")
                result = retriever.search(args.query)
                print(render_results([{"tool": "retrieve_policy", "result": result}]))
            else:
                from .evaluation import evaluate
                report = evaluate(retriever, output=args.output)
                print(json.dumps(report["metrics"], indent=2))
                print("Evaluation saved to " + args.output)
        except PolicyError as exc:
            parser.exit(1, f"Policy operation failed ({exc}). Assignments remain blocked until retrieval works.\n")
        return
    if args.command == "seed":
        created = seed_database(args.db)
        crm = CRM(args.db)
        print(f"Seed data updated: {len(crm.search_leads(SearchLeads()))} "
              f"fictional leads and {len(crm.list_agents())} sales agents. Existing data preserved."
              if created else "CRM already seeded; data preserved.")
        return
    if args.command == "demo-db":
        if args.db == os.getenv("CRM_DB_PATH", "data/crm.sqlite3"):
            parser.error("demo-db requires an explicit --db pointing to a NEW separate database")
        try:
            print("Created fictional demo CRM: " + str(create_demo_database(args.db)))
        except FileExistsError:
            parser.error("Refusing to replace an existing database. Choose a new --db path.")
        return
    if not os.path.isfile(args.db):
        parser.error("CRM database is missing. Run the seed command first.")
    if args.command in {"migrate", "setup-demo-identity"}:
        if args.command == "migrate":
            migrate_database(args.db)
            print("Stage 3 additive schema ready. Existing rows preserved; no permissions or facts granted.")
        else:
            setup_demo_identity(args.db)
            print("SIMULATED demo-dispatcher provisioned: first-assignment permission only, no overrides. "
                  "Existing lead/agent metadata unchanged. This is not production authentication.")
        return
    actor_id = "demo-dispatcher" if args.demo_actor else None
    if args.command == "result":
        if not args.operation_id or not args.lead_id or not args.assignee_id:
            parser.error("result requires --operation-id, --lead-id and --assignee-id")
        if actor_id:
            print("SIMULATED identity: demo-dispatcher. Not production authentication.")
        service = AssignmentService(CRM(args.db), get_default_retriever(), actor_id=actor_id)
        result = service.lookup_completed(AssignLead(lead_id=args.lead_id, assignee_id=args.assignee_id,
                                                     request_key=args.operation_id))
        if result is None:
            print("No committed result for that operation. Nothing executed; a fresh approval is needed to attempt it.")
        else:
            print(render_results([{"tool": "assign_lead", "result": result}]))
        return
    try:
        config = load_ranking_config(args.ranking_config) if args.ranking_config else None
        crm = CRM(args.db, ranking_config=config)
        copilot = Copilot(crm, create_model(), thread_id=args.thread_id or str(uuid4()),
                          max_steps=int(os.getenv("MAX_AGENT_STEPS", "8")), actor_id=actor_id)
    except RankingError:
        parser.error("Invalid --ranking-config. Use supported recorded factors and nonnegative finite weights; see docs/lead_ranking.md.")
    except ValueError:
        parser.error("Check LLM_MODEL, LLM_PROVIDER, credentials and MAX_AGENT_STEPS (1..20); see .env.example.")
    print("AI Sales Copilot | thread_id=" + copilot.config["configurable"]["thread_id"])
    print("In-memory checkpoints last for this process. Type /quit to exit; /retry resolves the last uncertain operation.")
    print("SIMULATED identity: demo-dispatcher. Not production authentication." if actor_id
          else "No actor identity: CRM reads work; assignments fail closed.")
    print("Fictional agents: " + json.dumps(crm.list_agents()))
    print(NOTICE)
    next_operation_id = args.operation_id
    if next_operation_id:
        print("First chat intent will reuse original operation ID: " + next_operation_id)
    try:
        while True:
            text = input("You> ").strip()
            if text == "/quit":
                break
            if not text:
                continue
            try:
                if text == "/retry":
                    try:
                        print("Copilot> " + copilot.retry_last()["answer"])
                    except RuntimeError as exc:
                        print(str(exc))
                    continue
                result = copilot.chat(text, operation_id=next_operation_id)
                next_operation_id = None
                print("Operation ID (keep for retries): " + result["operation_id"])
                while result.get("__interrupt__"):
                    payload = result["__interrupt__"][0].value
                    evidence = {"ok": True, "policy_refs": payload["policy_refs"],
                                "corpus_fingerprint": payload["corpus_fingerprint"], "notice": payload["notice"]}
                    try:
                        grounded = get_default_retriever().hydrate(evidence)
                    except PolicyError:
                        print("Policy evidence is no longer valid. Assignment rejected.")
                        result = copilot.resume({"approved": False})
                        continue
                    print(render_results([{"tool": "retrieve_policy", "result": grounded}]))
                    print("Approval required: " + json.dumps({"tool": payload["tool"], "action": payload["action"],
                                                             "binding": payload["approval_binding"]}, ensure_ascii=False))
                    decision = approval_decision(input("Type approve to execute; anything else rejects> "))
                    result = copilot.resume(decision)
                print("Copilot> " + result["answer"])
            except Exception as exc:
                # Provider/DB exceptions may contain sensitive URLs or request data.
                print(f"Run failed ({type(exc).__name__}); no success is being reported. Restart to begin a new session.")
                break
    except (EOFError, KeyboardInterrupt):
        print("\nSession ended. Pending approvals are not executed.")


if __name__ == "__main__":
    main()
