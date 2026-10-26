import pytest

from sales_copilot import model


@pytest.mark.parametrize("model_name", ["gemini-3.5-flash-lite", "gemini-3.8-flash"])
def test_default_provider_uses_gemini_developer_api(monkeypatch, model_name):
    monkeypatch.setattr(model, "load_dotenv", lambda **kwargs: None)
    monkeypatch.delenv("LLM_PROVIDER", raising=False)
    monkeypatch.setenv("LLM_MODEL", model_name)
    monkeypatch.delenv("GEMINI_THINKING_LEVEL", raising=False)
    captured = {}

    def init(name, **kwargs):
        captured.update(name=name, **kwargs)
        return "configured-model"

    monkeypatch.setattr(model, "init_chat_model", init)
    assert model.create_model() == "configured-model"
    assert captured == {"name": model_name, "model_provider": "google_genai",
                        "vertexai": False, "timeout": 60, "max_retries": 1,
                        "max_tokens": 2048, "thinking_level": "low"}


@pytest.mark.parametrize("provider,model_name", [("google_genai", "gemini-2.5-flash"), ("openai", "custom-model")])
def test_gemini3_thinking_settings_are_not_sent_to_other_model_families(monkeypatch, provider, model_name):
    monkeypatch.setattr(model, "load_dotenv", lambda **kwargs: None)
    monkeypatch.setenv("LLM_PROVIDER", provider)
    monkeypatch.setenv("LLM_MODEL", model_name)
    captured = {}
    monkeypatch.setattr(model, "init_chat_model", lambda name, **kwargs: captured.update(kwargs))
    model.create_model()
    assert "thinking_level" not in captured
    if provider == "openai":
        assert "vertexai" not in captured and "max_tokens" not in captured
