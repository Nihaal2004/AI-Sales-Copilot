"""Read-only resume parents and immutable per-trial checkpoints."""

import json
from pathlib import Path

from .availability import trial_availability


def write_json(path, value):
    path = Path(path)
    if path.exists():
        raise FileExistsError(path)
    temporary = path.with_suffix(path.suffix + ".pending")
    with temporary.open("x", encoding="utf-8") as stream:
        stream.write(json.dumps(value, indent=2, ensure_ascii=False) + "\n")
    temporary.rename(path)


def read_parent(directory):
    root = Path(directory)
    # Modern runs have an initial manifest even when the process was killed.
    path = root / "results.json"
    if not path.exists() and not (root / "run.json").exists():
        raise ValueError("resume_missing_manifest")
    header = json.loads((path if path.exists() else root / "run.json").read_text(encoding="utf-8"))
    if path.exists():
        results = header["results"]
    elif (root / "trials").exists():
        carried = root / "carried_results.json"
        results = json.loads(carried.read_text(encoding="utf-8")) if carried.exists() else []
        results += [json.loads(p.read_text(encoding="utf-8")) for p in sorted((root / "trials").glob("*.json"))]
    else:
        results = json.loads((root / "partial_results.json").read_text(encoding="utf-8"))
    keys = [(r["scenario_id"], r["trial"]) for r in results]
    if len(set(keys)) != len(keys):
        raise ValueError("duplicate_parent_trial")
    return header, results


def resumable_results(results):
    # Completed genuine contract failures are retained as failures. Resume is
    # not an opportunity to selectively resample them until the score improves.
    return [r for r in results if not trial_availability(r)["resume_incomplete"]]
