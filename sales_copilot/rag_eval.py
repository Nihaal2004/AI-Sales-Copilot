"""Separate offline-first policy evaluation; no agent or CRM dependency.

prepare reuses the existing labelled retrieval evaluation unchanged. judge needs
recorded responses and explicit consent to run an explicitly supplied evaluator.
"""

import argparse
import asyncio
import hashlib
import importlib
import importlib.metadata
import json
import math
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from .corpus import Corpus
from .evaluation import DEFAULT_QUERIES, evaluate
from .observability import local_only
from .retrieval import EMBED_MODEL, RERANK_MODEL, PolicyRetriever
from .tracing import Trace

METRICS = ("faithfulness", "response_relevancy", "context_utilization")


def file_hash(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def write_json(path, value):
    with Path(path).open("x", encoding="utf-8") as stream:
        json.dump(value, stream, indent=2, ensure_ascii=False, allow_nan=False)
        stream.write("\n")


def new_run(output_dir, run_id=None):
    run_id = run_id or str(uuid4())
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", run_id):
        raise ValueError("invalid_run_id")
    target = Path(output_dir) / run_id
    target.mkdir(parents=True, exist_ok=False)
    return target


def cached_retriever():
    """Only use already cached model files; never download or use paid APIs."""
    import torch
    from sentence_transformers import CrossEncoder, SentenceTransformer

    torch.set_num_threads(min(4, torch.get_num_threads()))
    cache = ".cache/policies/models"
    return PolicyRetriever(trace=Trace(None),
        encoder=SentenceTransformer(EMBED_MODEL, device="cpu", cache_folder=cache,
                                    trust_remote_code=False, local_files_only=True),
        reranker=CrossEncoder(RERANK_MODEL, device="cpu", cache_folder=cache,
                              activation_fn=torch.nn.Identity(), trust_remote_code=False,
                              local_files_only=True))


def skipped(reason):
    return {"status": "skipped", "reason": reason, "value": None}


def prepare(*, output_dir="docs/rag_runs", run_id=None, retriever=None):
    target = new_run(output_dir, run_id)
    with local_only():
        retriever = retriever if retriever is not None else cached_retriever()
        # This is exactly the existing retrieval evaluator: same labels, ranks,
        # thresholds, duplicate passage positions and negative-query semantics.
        retrieval = evaluate(retriever, output=target / "retrieval.json")
        samples = []
        for query in retrieval["queries"]:
            citations = query["applicable_citations"]
            refs = [r for r in query["rankings"]["reranked"] if r["citation"] in citations]
            samples.append({"id": query["id"], "user_input": query["query"],
                            "retrieved_refs": refs,
                            "retrieved_contexts": [retriever.corpus.resolve(r).text for r in refs]})
        write_json(target / "samples.json", samples)
        report = {"schema_version": 1, "phase": "prepare", "status": "completed",
                  "created_at": datetime.now(timezone.utc).isoformat(),
                  "corpus_fingerprint": retriever.corpus.fingerprint,
                  "labels_sha256": file_hash(DEFAULT_QUERIES),
                  "samples_sha256": file_hash(target / "samples.json"),
                  "sample_count": len(samples), "deterministic_retrieval": retrieval["metrics"],
                  "negative_queries": retrieval["negative_queries"],
                  "negative_abstentions": retrieval["negative_abstentions"],
                  "llm_judged": {m: skipped("responses_and_explicit_judge_opt_in_required") for m in METRICS},
                  "reference_answer_metrics": skipped("no_reference_answers_in_existing_labels"),
                  "telemetry_exported": False, "paid_api_calls": 0}
        write_json(target / "report.json", report)
    return report, target


def load_samples(prepared):
    root = Path(prepared)
    report = json.loads((root / "report.json").read_text(encoding="utf-8"))
    corpus = Corpus()  # Only the checked-in fictional corpus is admitted.
    if (report.get("phase") != "prepare" or report.get("status") != "completed" or
            report["corpus_fingerprint"] != corpus.fingerprint or
            report["labels_sha256"] != file_hash(DEFAULT_QUERIES) or
            report["samples_sha256"] != file_hash(root / "samples.json")):
        raise ValueError("prepared_dataset_changed_or_stale")
    samples = json.loads((root / "samples.json").read_text(encoding="utf-8"))
    labels = json.loads(DEFAULT_QUERIES.read_text(encoding="utf-8"))
    if [s["id"] for s in samples] != [q["id"] for q in labels]:
        raise ValueError("prepared_question_ids_mismatch")
    for sample, label in zip(samples, labels):
        contexts = [corpus.resolve(ref).text for ref in sample["retrieved_refs"]]
        if sample["user_input"] != label["query"] or sample["retrieved_contexts"] != contexts:
            raise ValueError("prepared_question_or_context_mismatch")
    return report, samples


def load_responses(path, manifest, samples):
    """Bind recorded outputs to this exact question/context snapshot."""
    supplied = json.loads(Path(path).read_text(encoding="utf-8"))
    if supplied.get("samples_sha256") != manifest["samples_sha256"]:
        raise ValueError("response_dataset_mismatch")
    by_id = {s["id"]: s for s in samples}
    responses = {}
    for row in supplied["responses"]:
        key = row["id"]
        if key not in by_id or key in responses:
            raise ValueError("unknown_or_duplicate_response_id")
        if (not isinstance(row.get("response"), str) or not row["response"].strip() or
                not isinstance(row.get("source"), str) or not row["source"].strip()):
            raise ValueError("response_and_source_required")
        if row.get("retrieved_citations") != [r["citation"] for r in by_id[key]["retrieved_refs"]]:
            raise ValueError("response_context_mismatch")
        responses[key] = row
    if not responses:
        raise ValueError("no_recorded_responses")
    return responses


def ragas_metrics(llm, embeddings):
    # Disable Ragas product analytics before importing its optional dependency.
    os.environ["RAGAS_DO_NOT_TRACK"] = "true"
    from ragas.metrics.collections import AnswerRelevancy, ContextUtilization, Faithfulness

    return {"faithfulness": Faithfulness(llm=llm),
            "response_relevancy": AnswerRelevancy(llm=llm, embeddings=embeddings),
            "context_utilization": ContextUtilization(llm=llm)}


async def score_samples(samples, responses, metrics, *, timeout=60):
    rows = []
    for sample in samples:
        response = responses.get(sample["id"])
        scores = {}
        for name in METRICS:
            if response is None:
                scores[name] = skipped("no_recorded_response")
                continue
            if not sample["retrieved_contexts"] and name != "response_relevancy":
                scores[name] = skipped("no_retrieved_contexts")
                continue
            arguments = {"user_input": sample["user_input"], "response": response["response"]}
            if name != "response_relevancy":
                arguments["retrieved_contexts"] = sample["retrieved_contexts"]
            try:
                result = await asyncio.wait_for(metrics[name].ascore(**arguments), timeout=timeout)
                value = float(result.value)
                if not math.isfinite(value):
                    raise ValueError("nonfinite_judge_score")
                scores[name] = {"status": "measured", "value": value}
            except Exception:
                # Exceptions may echo prompts, response prose or provider secrets.
                scores[name] = {"status": "error", "reason": "judge_metric_failed", "value": None}
        rows.append({"id": sample["id"], "metrics": scores})
    return rows


def judge(*, prepared, responses_path, judge_factory, allow_llm_judge=False,
          output_dir="docs/rag_runs", run_id=None, timeout=60):
    if not allow_llm_judge:
        raise ValueError("explicit_allow_llm_judge_required")
    if not math.isfinite(timeout) or timeout <= 0:
        raise ValueError("positive_finite_timeout_required")
    manifest, samples = load_samples(prepared)
    responses = load_responses(responses_path, manifest, samples)
    target = new_run(output_dir, run_id)
    write_json(target / "run.json", {"phase": "judge", "status": "started",
                                     "samples_sha256": manifest["samples_sha256"],
                                     "responses_sha256": file_hash(responses_path)})
    with local_only():
        os.environ["RAGAS_DO_NOT_TRACK"] = "true"
        # No implicit OpenAI/Gemini defaults: the trusted factory explicitly
        # supplies both judge and embeddings, and may use local models instead.
        try:
            llm, embeddings = judge_factory()
            metrics = ragas_metrics(llm, embeddings)
            rows = asyncio.run(score_samples(samples, responses, metrics, timeout=timeout))
        except Exception:
            write_json(target / "failure.json", {"phase": "judge", "status": "failed",
                                                 "reason": "judge_setup_or_execution_failed"})
            raise RuntimeError("judge_setup_or_execution_failed") from None
    summary = {}
    for name in METRICS:
        scores = [r["metrics"][name] for r in rows]
        values = [s["value"] for s in scores if s["status"] == "measured"]
        summary[name] = {"measured": len(values),
                         "skipped": sum(s["status"] == "skipped" for s in scores),
                         "errors": sum(s["status"] == "error" for s in scores),
                         "mean": sum(values) / len(values) if values else None}
    report = {"schema_version": 1, "phase": "judge", "status": "completed",
              "ragas_version": importlib.metadata.version("ragas"),
              "judge_type": type(llm).__name__, "embedding_type": type(embeddings).__name__,
              "judge_model": getattr(llm, "model_name", None),
              "embedding_model": getattr(embeddings, "model_name", None),
              "metric_timeout_seconds": timeout,
              "samples_sha256": manifest["samples_sha256"],
              "responses_sha256": file_hash(responses_path),
              "deterministic_retrieval": manifest["deterministic_retrieval"],
              "llm_judged": summary, "queries": rows,
              "reference_answer_metrics": skipped("no_reference_answers_in_existing_labels"),
              "telemetry_exported": False}
    write_json(target / "report.json", report)
    return report, target


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("phase", choices=["prepare", "judge"])
    parser.add_argument("--output-dir", default="docs/rag_runs")
    parser.add_argument("--run-id")
    parser.add_argument("--prepared")
    parser.add_argument("--responses")
    parser.add_argument("--judge-factory", help="Trusted module:function returning (Ragas LLM, embeddings)")
    parser.add_argument("--allow-llm-judge", action="store_true",
                        help="Explicitly execute the configured judge; it may incur API cost")
    parser.add_argument("--metric-timeout", type=float, default=60)
    args = parser.parse_args()
    try:
        if args.phase == "prepare":
            report, target = prepare(output_dir=args.output_dir, run_id=args.run_id)
        else:
            if not (args.allow_llm_judge and args.prepared and args.responses and args.judge_factory):
                parser.error("judge requires --allow-llm-judge, --prepared, --responses and --judge-factory")
            module, function = args.judge_factory.split(":", 1)
            # Import factory only after validating the data and explicit opt-in.
            def factory():
                return getattr(importlib.import_module(module), function)()
            report, target = judge(prepared=args.prepared, responses_path=args.responses,
                judge_factory=factory, allow_llm_judge=True, output_dir=args.output_dir,
                run_id=args.run_id, timeout=args.metric_timeout)
    except (ValueError, RuntimeError, FileExistsError, OSError, ImportError):
        parser.exit(1, "Evaluation failed: check dataset, new output path and optional dependencies.\n")
    print(json.dumps({"deterministic_retrieval": report["deterministic_retrieval"],
                      "llm_judged": report["llm_judged"]}, indent=2))
    print("Artifacts: " + str(target))


if __name__ == "__main__":
    main()
