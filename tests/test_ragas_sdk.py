"""Optional SDK contract tests; typed evaluator doubles, no network or scores claimed."""

import asyncio
import importlib.util
import os

import pytest

os.environ["RAGAS_DO_NOT_TRACK"] = "true"
if importlib.util.find_spec("ragas") is None:
    pytest.skip("optional Ragas evaluation environment required", allow_module_level=True)

from ragas.embeddings.base import BaseRagasEmbedding
from ragas.llms.base import InstructorBaseRagasLLM

from sales_copilot.rag_eval import METRICS, ragas_metrics, score_samples


class TypedJudge(InstructorBaseRagasLLM):
    """Scripted schema outputs test Ragas' real decomposition/scoring code."""
    def generate(self, prompt, response_model):
        outputs = {
            "StatementGeneratorOutput": {"statements": ["fictional statement"]},
            "NLIStatementOutput": {"statements": [{"statement": "fictional statement",
                                                   "reason": "test verdict", "verdict": 1}]},
            "AnswerRelevanceOutput": {"question": "test question", "noncommittal": 0},
            "ContextPrecisionOutput": {"reason": "test verdict", "verdict": 1},
        }
        return response_model.model_validate(outputs[response_model.__name__])

    async def agenerate(self, prompt, response_model):
        return self.generate(prompt, response_model)


class TypedEmbeddings(BaseRagasEmbedding):
    def embed_text(self, text, **kwargs):
        return [1.0, 0.0]

    async def aembed_text(self, text, **kwargs):
        return self.embed_text(text)


def test_real_ragas_collections_accept_adapters_and_expected_sample_fields():
    metrics = ragas_metrics(TypedJudge(), TypedEmbeddings())
    rows = asyncio.run(score_samples(
        [{"id": "test-only", "user_input": "test question", "retrieved_contexts": ["fictional statement"]}],
        {"test-only": {"response": "fictional statement"}}, metrics))
    assert set(rows[0]["metrics"]) == set(METRICS)
    assert all(s["status"] == "measured" for s in rows[0]["metrics"].values())


def test_builtin_gemini_factory_reuses_existing_adapter_without_calling_it(monkeypatch):
    import numpy as np
    from pydantic import BaseModel
    from sales_copilot.rag_judges import gemini

    initialized = []
    calls = []
    class Answer(BaseModel):
        text: str
    class Structured:
        def invoke(self, prompt):
            calls.append(prompt)
            return Answer(text="test response")
        async def ainvoke(self, prompt):
            return self.invoke(prompt)
    class Model:
        def with_structured_output(self, schema, **kwargs):
            assert schema is Answer and kwargs == {"method": "json_schema"}
            return Structured()
    class Encoder:
        def __init__(self, *args, **kwargs):
            assert kwargs["local_files_only"] is True
        def encode(self, texts, **kwargs):
            return np.ones((len(texts), 2))
    monkeypatch.setenv("RAGAS_JUDGE_MODEL", "test-gemini-model")
    monkeypatch.setattr("dotenv.load_dotenv", lambda **kwargs: None)
    monkeypatch.setattr("sentence_transformers.SentenceTransformer", Encoder)
    monkeypatch.setattr("langchain.chat_models.init_chat_model",
                        lambda *args, **kwargs: initialized.append((args, kwargs)) or Model())
    llm, embeddings = gemini()
    assert not calls and initialized[0][1]["model_provider"] == "google_genai"
    assert llm.model_name == "test-gemini-model"
    assert llm.generate("test prompt", Answer).text == "test response"
    assert asyncio.run(llm.agenerate("test prompt", Answer)).text == "test response"
    assert len(asyncio.run(embeddings.aembed_texts(["one", "two"]))) == 2
