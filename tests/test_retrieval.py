import hashlib
import json
import shutil

import numpy as np
import pytest

from sales_copilot.corpus import DEFAULT_CORPUS, Corpus, PolicyError, split_section
from sales_copilot.evaluation import query_metrics, DEFAULT_QUERIES
from sales_copilot.retrieval import PolicyRetriever, rrf, tokenize
from sales_copilot.tracing import Trace


class TinyEncoder:
    """Offline deterministic backend, not used for reported semantic metrics."""
    def __init__(self):
        self.calls = []

    def encode(self, texts, **kwargs):
        self.calls.append(list(texts))
        vectors = np.zeros((len(texts), 128), dtype=np.float32)
        for i, text in enumerate(texts):
            for token in tokenize(text):
                j = int(hashlib.sha256(token.encode()).hexdigest()[:8], 16) % 128
                vectors[i, j] += 1
        return vectors / np.maximum(np.linalg.norm(vectors, axis=1, keepdims=True), 1e-9)


class TinyReranker:
    def __init__(self):
        self.pairs = []

    def predict(self, pairs, **kwargs):
        self.pairs.extend(pairs)
        return np.array([len(set(tokenize(q)) & set(tokenize(t))) - 0.5 for q, t in pairs])


def small_retriever(tmp_path, *, corpus=DEFAULT_CORPUS, trace=None):
    return PolicyRetriever(corpus_dir=corpus, cache_dir=tmp_path / "cache", encoder=TinyEncoder(),
                           reranker=TinyReranker(), trace=trace or Trace(None))


def test_corpus_is_complete_substantial_and_citations_are_stable():
    first, second = Corpus(), Corpus()
    assert len(first.documents) == 10 and len(first.chunks) == 60
    assert sum(len(c.text.split()) for c in first.chunks) > 4500
    assert {c.chunk_id for c in first.chunks} == {c.chunk_id for c in second.chunks}
    assert all(len(c.text.split()) <= 170 for c in first.chunks)
    assert all(first.resolve(c.reference()) == c for c in first.chunks)
    assert len(split_section("word " * 400)) == 3


def test_rrf_uses_independent_ranks_and_does_not_sum_incomparable_raw_scores():
    scores = rrf([[1, 2], [2, 3]])
    assert scores[2] == pytest.approx(1 / 62 + 1 / 61)
    assert max(scores, key=scores.get) == 2
    assert rrf([[1, 1]])[1] == pytest.approx(1 / 61)


def test_pipeline_returns_grounded_passages_and_all_four_safe_diagnostics(tmp_path):
    lines = []
    retriever = small_retriever(tmp_path, trace=Trace(lines.append))
    result = retriever.search("ownership assignment secret-api-key", top_k=3)
    assert result["ok"] and len(result["policy_refs"]) <= 3
    for ref, passage in zip(result["policy_refs"], result["passages"]):
        assert passage["text"] == retriever.corpus.resolve(ref).text
        assert all(np.isfinite(ref[key]) for key in ("dense_score", "bm25_score", "rrf_score", "cross_score"))
    stages = [json.loads(line[6:])["result"]["stage"] for line in lines]
    assert stages == ["bm25", "dense", "fused", "reranked"]
    assert "secret-api-key" not in "\n".join(lines)
    assert '"score": "<redacted>"' not in "\n".join(lines)
    assert len(retriever.reranker.pairs) <= 12


def test_corpus_vectors_cached_in_memory_and_on_disk(tmp_path):
    first = small_retriever(tmp_path)
    first.rank("ownership")
    first.rank("territory")
    assert sum(len(call) == 60 for call in first.encoder.calls) == 1
    second = small_retriever(tmp_path)
    second.prepare()
    assert second.cache_hit and second.encoder.calls == []
    assert np.allclose(first._vectors, second._vectors)


def test_changed_corpus_invalidates_embedding_cache_and_citations(tmp_path):
    corpus_path = tmp_path / "policies"
    shutil.copytree(DEFAULT_CORPUS, corpus_path)
    first = small_retriever(tmp_path, corpus=corpus_path)
    first.prepare()
    old = first.corpus.chunks[0].reference()
    path = corpus_path / "availability.json"
    doc = json.loads(path.read_text())
    doc["sections"][0]["text"] += " This section was updated."
    path.write_text(json.dumps(doc), encoding="utf-8")
    second = small_retriever(tmp_path, corpus=corpus_path)
    second.prepare()
    assert not second.cache_hit and second.corpus.fingerprint != first.corpus.fingerprint
    with pytest.raises(PolicyError):
        second.corpus.resolve(old)


def test_corrupt_vector_cache_rebuilds_without_pickle(tmp_path):
    first = small_retriever(tmp_path)
    first.prepare()
    next((tmp_path / "cache").glob("*.npz")).write_bytes(b"corrupt cache")
    second = small_retriever(tmp_path)
    second.prepare()
    assert not second.cache_hit and second.encoder.calls


def test_unrelated_query_abstains_and_does_not_return_arbitrary_nearest_neighbours(tmp_path):
    retriever = small_retriever(tmp_path)
    result = retriever.search("xyzzqnebula croissantquasar")
    assert result["ok"] is False and result["passages"] == []
    assert result["error"] == "policy_evidence_not_found"


def test_encoder_failure_does_not_silently_fall_back_to_bm25(tmp_path):
    retriever = small_retriever(tmp_path)
    def fail(*args, **kwargs):
        raise RuntimeError("private endpoint payload")
    retriever.encoder.encode = fail
    with pytest.raises(PolicyError, match="policy_models_or_index_unavailable"):
        retriever.search("ownership")


def test_nonfinite_scores_cannot_qualify_as_evidence(tmp_path):
    retriever = small_retriever(tmp_path)
    result = retriever.search("ownership")
    result["policy_refs"][0]["cross_score"] = float("nan")
    with pytest.raises(PolicyError, match="policy_citation_invalid"):
        retriever.compact(result)


def test_metric_definitions_count_passage_positions_including_duplicate_policy_chunks():
    ranks = [{"policy_id": "A"}, {"policy_id": "A"}, {"policy_id": "A"}, {"policy_id": "B"}]
    assert query_metrics(ranks, {"A", "B"}) == {"recall_at_3": 0.5, "mrr_at_5": 1.0}
    assert query_metrics(ranks, {"B"}) == {"recall_at_3": 0.0, "mrr_at_5": 0.25}
    assert query_metrics(ranks, {"C"}) == {"recall_at_3": 0.0, "mrr_at_5": 0.0}


def test_manual_labels_cover_all_policies_and_at_least_twenty_positive_queries():
    queries = json.loads(DEFAULT_QUERIES.read_text())
    assert len([q for q in queries if q["expected_policy_ids"]]) >= 20
    assert {p for q in queries for p in q["expected_policy_ids"]} == {d.policy_id for d in Corpus().documents}
