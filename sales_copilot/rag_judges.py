"""Explicit Ragas evaluator factories; importing this module never calls a model."""

import asyncio
import os


def gemini():
    """Existing Gemini adapter + cached local embeddings, only after judge opt-in.

    Implements Ragas' modern interfaces directly so we can reuse LangChain's
    working Gemini structured output without another provider wrapper or proxy.
    """
    from dotenv import load_dotenv

    load_dotenv(override=False)
    model_name = os.getenv("RAGAS_JUDGE_MODEL", "").strip()
    if not model_name:
        raise ValueError("explicit_RAGAS_JUDGE_MODEL_required")
    os.environ["RAGAS_DO_NOT_TRACK"] = "true"
    from ragas.embeddings.base import BaseRagasEmbedding
    from ragas.llms.base import InstructorBaseRagasLLM
    from sentence_transformers import SentenceTransformer
    from langchain.chat_models import init_chat_model

    from .retrieval import EMBED_MODEL

    encoder = SentenceTransformer(EMBED_MODEL, device="cpu", cache_folder=".cache/policies/models",
                                  trust_remote_code=False, local_files_only=True)
    model = init_chat_model(model_name, model_provider="google_genai", vertexai=False,
                            timeout=60, max_retries=1, max_tokens=4096, temperature=0)

    class GeminiJudge(InstructorBaseRagasLLM):
        @property
        def model_name(self):
            return model_name

        def generate(self, prompt, response_model):
            return model.with_structured_output(response_model, method="json_schema").invoke(prompt)

        async def agenerate(self, prompt, response_model):
            return await model.with_structured_output(response_model, method="json_schema").ainvoke(prompt)

    class CachedEmbeddings(BaseRagasEmbedding):
        model_name = EMBED_MODEL

        def embed_text(self, text, **kwargs):
            return self.embed_texts([text])[0]

        def embed_texts(self, texts, **kwargs):
            return encoder.encode(texts, normalize_embeddings=True, show_progress_bar=False).tolist()

        async def aembed_text(self, text, **kwargs):
            return await asyncio.to_thread(self.embed_text, text)

        async def aembed_texts(self, texts, **kwargs):
            return await asyncio.to_thread(self.embed_texts, texts)

    return GeminiJudge(), CachedEmbeddings()
