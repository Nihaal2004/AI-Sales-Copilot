"""Analyze a saved report only. Never imports/constructs a provider model."""

import argparse
import hashlib
import json
from pathlib import Path

from sales_copilot.agent_eval.availability import availability_summary
from sales_copilot.agent_eval.artifacts import write_json


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("run_directory", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    path = args.run_directory / "results.json"
    report = json.loads(path.read_text(encoding="utf-8"))
    failures = []
    for trial in report["results"]:
        injected_remaining = trial.get("fault") == "model_timeout_once"
        for event in trial["trajectory"]:
            if event["kind"] != "llm_call_failed":
                continue
            result = event.get("result", {})
            injected = injected_remaining and result.get("error") == "model_timeout"
            if injected:
                injected_remaining = False
            failures.append({"scenario_id": trial["scenario_id"], "trial": trial["trial"],
                             "sequence": event["sequence"], "error": result.get("error"),
                             "http_status": result.get("http_status"), "injected": injected,
                             "quota_type": "unknown" if result.get("http_status") == 429 else None,
                             "trial_passed": trial["passed"]})
    derived = {"analysis_only": True, "provider_calls_made": 0, "run_id": report["run_id"],
               "source_results_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
               "original_summary": report["summary"], "availability": availability_summary(report["results"]),
               "failed_llm_calls": failures}
    write_json(args.output, derived)
    print(json.dumps(derived["availability"], indent=2))


if __name__ == "__main__":
    main()
