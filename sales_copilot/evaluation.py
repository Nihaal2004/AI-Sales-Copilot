"""Reproducible labelled retrieval evaluation using actual local models."""

import importlib.metadata
import json
from pathlib import Path

from .retrieval import EMBED_MODEL, RERANK_MODEL

DEFAULT_QUERIES = Path(__file__).resolve().parent.parent / "evaluation" / "policy_queries.json"


def query_metrics(ranking: list[dict], expected: set[str]) -> dict:
    if not expected:
        return {"recall_at_3": None, "mrr_at_5": None}
    found = {r["policy_id"] for r in ranking[:3]}
    reciprocal = next((1.0 / i for i, r in enumerate(ranking[:5], 1) if r["policy_id"] in expected), 0.0)
    return {"recall_at_3": len(found & expected) / len(expected), "mrr_at_5": reciprocal}


def evaluate(retriever, queries_path=DEFAULT_QUERIES, output="docs/stage2_evaluation.json") -> dict:
    queries = json.loads(Path(queries_path).read_text(encoding="utf-8"))
    stages = ("bm25", "dense", "fused", "reranked")
    results = []
    for query in queries:
        rankings = retriever.rank(query["query"])
        expected = set(query["expected_policy_ids"])
        results.append({**query, "rankings": {stage: rankings[stage][:5] for stage in stages},
                        "metrics": {stage: query_metrics(rankings[stage], expected) for stage in stages},
                        "applicable_citations": [r["citation"] for r in retriever.applicable(rankings["reranked"])[:3]]})
    positives = [r for r in results if r["expected_policy_ids"]]
    negatives = [r for r in results if not r["expected_policy_ids"]]
    metrics = {stage: {key: sum(r["metrics"][stage][key] for r in positives) / len(positives)
                       for key in ("recall_at_3", "mrr_at_5")} for stage in stages}
    report = {"corpus_fingerprint": retriever.corpus.fingerprint,
              "policies": len(retriever.corpus.documents), "chunks": len(retriever.corpus.chunks),
              "embedding_model": EMBED_MODEL, "cross_encoder": RERANK_MODEL,
              "versions": {p: importlib.metadata.version(p) for p in ("sentence-transformers", "transformers", "numpy", "torch", "rank-bm25")},
              "rrf_k": 60, "candidate_k": 12, "dense_threshold": retriever.min_dense,
              "cross_logit_threshold": retriever.min_cross, "positive_queries": len(positives),
              "negative_queries": len(negatives), "negative_abstentions": sum(not r["applicable_citations"] for r in negatives),
              "definition": "Top 3/5 passage positions; expected IDs are policy IDs. Duplicate-policy chunks occupy positions. Negative queries excluded from recall/MRR.",
              "metrics": metrics, "queries": results}
    path = Path(output)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    return report
