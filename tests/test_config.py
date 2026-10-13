import pytest

from agent.config import load_settings


def test_loads_gemini_settings(monkeypatch):
    monkeypatch.setattr("agent.config.load_dotenv", lambda: None)
    monkeypatch.setenv("LLM_PROVIDER", "Gemini")
    monkeypatch.setenv("GEMINI_API_KEY", "test-api-key")
    monkeypatch.delenv("GEMINI_MODEL", raising=False)

    settings = load_settings()

    assert settings.llm_provider == "gemini"
    assert settings.llm_enabled
    assert settings.gemini_api_key == "test-api-key"
    assert settings.gemini_model == "gemini-3.8-flash"


def test_rejects_unsupported_llm_provider(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "unsupported")

    with pytest.raises(ValueError, match="Unsupported LLM_PROVIDER"):
        load_settings()
