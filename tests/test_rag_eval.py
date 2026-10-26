import asyncio
import json
from types import SimpleNamespace

import pytest

from sales_copilot import rag_eval
from test_retrieval import small_retriever


@pytest.fixture
def prepared(tmp_path):
    report, target = rag_eval.prepare(output_dir=tmp_path / "runs", run_id="prepared",
                                     retriever=small_retriever(tmp_path))
    return report, target


def responses_file(tmp_path, manifest, samples, ids=None):
    rows = [{"id": s["id"], "response": "Authored test response; not a benchmark answer.",
             "source": "unit-test-only", "retrieved_citations": [r["citation"] for r in s["retrieved_refs"]]}
            for s in samples if ids is None or s["id"] in ids]
    path = tmp_path / "responses.json"
    path.write_text(json.dumps({"samples_sha256": manifest["samples_sha256"], "responses": rows}), encoding="utf-8")
    return path


def test_prepare_keeps_existing_labels_metrics_and_canonical_contexts(prepared):
    report, target = prepared
    manifest, samples = rag_eval.load_samples(target)
    retrieval = json.loads((target / "retrieval.json").read_text(encoding="utf-8"))
    labels = json.loads(rag_eval.DEFAULT_QUERIES.read_text(encoding="utf-8"))
    assert report["deterministic_retrieval"] == retrieval["metrics"]
    assert len(samples) == len(labels) == 31
    assert report["negative_queries"] == 3
    assert all(s["status"] == "skipped" and s["value"] is None for s in report["llm_judged"].values())
    assert report["paid_api_calls"] == 0 and not report["telemetry_exported"]
    assert all("response" not in s and "reference" not in s for s in samples)
    assert all(s["user_input"] == label["query"] for s, label in zip(samples, labels))


def test_never_replace_prior_results_or_allow_path_escape(prepared, tmp_path):
    _, target = prepared
    before = {p.name: p.read_bytes() for p in target.iterdir()}
    with pytest.raises(FileExistsError):
        rag_eval.prepare(output_dir=target.parent, run_id=target.name)
    with pytest.raises(ValueError, match="invalid_run_id"):
        rag_eval.new_run(tmp_path, "../escape")
    assert before == {p.name: p.read_bytes() for p in target.iterdir()}


def test_judging_requires_opt_in_before_factory_or_outputs(prepared, tmp_path):
    _, target = prepared
    called = []
    with pytest.raises(ValueError, match="explicit_allow"):
        rag_eval.judge(prepared=target, responses_path=tmp_path / "missing.json",
                       judge_factory=lambda: called.append(True))
    assert not called


def test_setup_failure_writes_sanitized_failure_without_scores(prepared, tmp_path):
    _, target = prepared
    manifest, samples = rag_eval.load_samples(target)
    path = responses_file(tmp_path, manifest, samples, ids={"Q01"})
    def broken_factory():
        raise RuntimeError("private credential from provider")
    with pytest.raises(RuntimeError, match="judge_setup_or_execution_failed"):
        rag_eval.judge(prepared=target, responses_path=path, judge_factory=broken_factory,
                       allow_llm_judge=True, output_dir=tmp_path / "runs", run_id="failed")
    failure = tmp_path / "runs" / "failed"
    assert not (failure / "report.json").exists()
    assert "private credential" not in (failure / "failure.json").read_text(encoding="utf-8")


def test_builtin_judge_has_no_default_model(monkeypatch):
    from sales_copilot.rag_judges import gemini

    monkeypatch.setattr("dotenv.load_dotenv", lambda **kwargs: None)
    monkeypatch.delenv("RAGAS_JUDGE_MODEL", raising=False)
    with pytest.raises(ValueError, match="explicit_RAGAS_JUDGE_MODEL_required"):
        gemini()


@pytest.mark.parametrize("field", ["user_input", "retrieved_contexts", "retrieved_refs"])
def test_context_or_question_tampering_rejected_even_with_updated_hash(prepared, field):
    report, target = prepared
    path = target / "samples.json"
    samples = json.loads(path.read_text(encoding="utf-8"))
    index = next(i for i, s in enumerate(samples) if s["retrieved_refs"])
    if field == "retrieved_refs":
        samples[index][field][0]["citation"] = "forged-citation"
    elif field == "retrieved_contexts":
        samples[index][field][0] = "private CRM body"
    else:
        samples[index][field] = "Unlabelled private query"
    path.write_text(json.dumps(samples), encoding="utf-8")
    report["samples_sha256"] = rag_eval.file_hash(path)
    (target / "report.json").write_text(json.dumps(report), encoding="utf-8")
    with pytest.raises((ValueError, RuntimeError)):
        rag_eval.load_samples(target)


@pytest.mark.parametrize("mutation", ["hash", "duplicate", "unknown", "source", "citations"])
def test_recorded_answers_must_match_exact_prepared_snapshot(prepared, tmp_path, mutation):
    _, target = prepared
    manifest, samples = rag_eval.load_samples(target)
    path = responses_file(tmp_path, manifest, samples, ids={"Q01"})
    supplied = json.loads(path.read_text(encoding="utf-8"))
    row = supplied["responses"][0]
    if mutation == "hash":
        supplied["samples_sha256"] = "wrong"
    elif mutation == "duplicate":
        supplied["responses"].append(dict(row))
    elif mutation == "unknown":
        row["id"] = "CRM-private-id"
    elif mutation == "source":
        row["source"] = ""
    else:
        row["retrieved_citations"] = ["forged"]
    path.write_text(json.dumps(supplied), encoding="utf-8")
    with pytest.raises(ValueError):
        rag_eval.load_responses(path, manifest, samples)


def test_judge_records_only_executed_scores_and_separates_denominators(prepared, tmp_path, monkeypatch):
    _, target = prepared
    manifest, samples = rag_eval.load_samples(target)
    path = responses_file(tmp_path, manifest, samples, ids={"Q01"})
    calls = []
    class Metric:
        def __init__(self, name):
            self.name = name
        async def ascore(self, **kwargs):
            calls.append((self.name, kwargs))
            if self.name == "faithfulness":
                raise RuntimeError("private provider error")
            return SimpleNamespace(value=0.5)
    monkeypatch.setattr(rag_eval, "ragas_metrics", lambda *args: {m: Metric(m) for m in rag_eval.METRICS})
    real_version = rag_eval.importlib.metadata.version
    monkeypatch.setattr(rag_eval.importlib.metadata, "version",
                        lambda name: "test-double" if name == "ragas" else real_version(name))
    report, output = rag_eval.judge(prepared=target, responses_path=path,
        judge_factory=lambda: (object(), object()), allow_llm_judge=True, output_dir=tmp_path / "runs", run_id="judged")
    summary = report["llm_judged"]
    assert summary["faithfulness"] == {"measured": 0, "skipped": 30, "errors": 1, "mean": None}
    assert summary["response_relevancy"] == {"measured": 1, "skipped": 30, "errors": 0, "mean": 0.5}
    assert summary["context_utilization"] == {"measured": 1, "skipped": 30, "errors": 0, "mean": 0.5}
    assert report["deterministic_retrieval"] == manifest["deterministic_retrieval"]
    assert "private provider error" not in (output / "report.json").read_text(encoding="utf-8")
    assert all("reference" not in arguments for _, arguments in calls)


def test_abstention_with_no_context_skips_grounding_metrics():
    class Metric:
        async def ascore(self, **kwargs):
            assert "retrieved_contexts" not in kwargs
            return SimpleNamespace(value=0.5)
    rows = asyncio.run(rag_eval.score_samples(
        [{"id": "N01", "user_input": "unrelated question", "retrieved_contexts": []}],
        {"N01": {"response": "no applicable policy"}}, {m: Metric() for m in rag_eval.METRICS}))
    scores = rows[0]["metrics"]
    assert scores["faithfulness"]["status"] == scores["context_utilization"]["status"] == "skipped"
    assert scores["response_relevancy"] == {"status": "measured", "value": 0.5}


@pytest.mark.parametrize("behavior", ["nan", "timeout"])
def test_nonfinite_scores_and_timeouts_are_errors_not_zero(behavior):
    class Metric:
        async def ascore(self, **kwargs):
            if behavior == "timeout":
                await asyncio.sleep(1)
            return SimpleNamespace(value=float("nan"))
    rows = asyncio.run(rag_eval.score_samples(
        [{"id": "Q01", "user_input": "question", "retrieved_contexts": ["fictional policy"]}],
        {"Q01": {"response": "test response"}}, {m: Metric() for m in rag_eval.METRICS}, timeout=0.001))
    assert all(s["status"] == "error" and s["value"] is None for s in rows[0]["metrics"].values())
