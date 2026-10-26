"""Offline guard regression: saved proposal plans must remain unused by host dispatch."""

import argparse
import hashlib
import json
from pathlib import Path
from unittest.mock import patch

from sales_copilot.agent_eval.artifacts import write_json
from sales_copilot.agent_eval.fixtures import ScriptedModel
from sales_copilot.agent_eval.runner import DATASET, aggregate, load_scenarios, markdown_report, run_scenario, source_fingerprint


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=Path("docs/stage4_runs/assignment-guard-replay"))
    args = parser.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=False)
    results, plans = [], {}
    for scenario in load_scenarios():
        sid = scenario["id"]
        if sid not in {"A03", "A08"}:
            continue
        action = scenario["expected"]["assignment"]
        reads = [{"tool": "retrieve_policy", "arguments": {"query": "ownership geographic assignment rules"}},
                 {"tool": "get_lead_details", "arguments": {"lead_id": action["lead_id"]}}]
        responses = (reads if sid == "A03" else [{"parallel": reads}]) + [
            {"final": True}, {"tool": "assign_lead", "arguments": {**action, "request_key": "ignored-by-host"}}]
        plans[sid] = responses
        # Replace only model responses. Requests, fixtures, labels, approvals and
        # graders are the original benchmark's; no real model is constructed.
        model = ScriptedModel(responses)
        with patch("sales_copilot.agent_eval.runner.ScriptedModel", lambda _: model):
            results.append(run_scenario(scenario, run_id=args.output_dir.name))
        assert model.call_count == 0, "Registered assignments must not invoke saved native proposal plans"
    report = {"schema_version": 2, "run_id": args.output_dir.name, "mode": "scripted",
              "execution_status": "completed", "provider_calls_made": 0,
              "basis": "gemini-full-30 native response plans retained but never invoked: host-dispatch regression, not a new Gemini response or a replay of model reasoning",
              "manifest": {"dataset_sha256": hashlib.sha256(DATASET.read_bytes()).hexdigest(),
                           "source_sha256": source_fingerprint()}, "scripted_response_plans": plans,
              "summary": aggregate(results), "results": results}
    write_json(args.output_dir / "results.json", report)
    (args.output_dir / "report.md").write_text(markdown_report(report), encoding="utf-8")
    with (args.output_dir / "trajectories.jsonl").open("x", encoding="utf-8") as stream:
        for result in results:
            for event in result["trajectory"]:
                stream.write(json.dumps(event) + "\n")
    print(json.dumps(report["summary"], indent=2))


if __name__ == "__main__":
    main()
