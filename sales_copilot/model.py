"""Real LangChain model initialization; credentials remain in the environment."""

import os

from dotenv import load_dotenv
from langchain.chat_models import init_chat_model


def create_model():
    load_dotenv(override=False)
    provider = os.getenv("LLM_PROVIDER", "google_genai")
    model_name = os.getenv("LLM_MODEL")
    if not model_name:
        raise ValueError("Set LLM_MODEL to an available native tool-calling model (see .env.example).")
    # The integration reads GOOGLE_API_KEY, falling back to GEMINI_API_KEY.
    # Select the Developer API explicitly, even if cloud/Vertex env vars are set.
    options = {}
    if provider == "google_genai":
        # CRM tool selection needs short responses, not an unrestricted reasoning
        # budget. Gemini 3's default thinking can add substantial request latency.
        options = {"vertexai": False, "max_tokens": 2048}
        if model_name.removeprefix("models/").startswith("gemini-3"):
            options["thinking_level"] = os.getenv("GEMINI_THINKING_LEVEL", "low")
    # Installed google-genai integration maps this to HttpRetryOptions(attempts=1):
    # one TOTAL attempt, leaving live-evaluation retries to QuotaController.
    return init_chat_model(model_name, model_provider=provider, timeout=60, max_retries=1, **options)
