from agent.config import Settings, load_settings


def test_defaults_work_without_llm():
    s = Settings()
    assert s.llm_provider == "none"
    assert s.llm_enabled is False
    assert 0 < s.confidence_threshold <= 1


def test_load_settings_reads_env(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "ollama")
    monkeypatch.setenv("CONFIDENCE_THRESHOLD", "0.9")
    s = load_settings()
    assert s.llm_enabled is True
    assert s.confidence_threshold == 0.9


def test_screenshot_dir_setting(monkeypatch):
    assert Settings().screenshot_dir == "screenshots"
    monkeypatch.setenv("SCREENSHOT_DIR", "elsewhere")
    assert load_settings().screenshot_dir == "elsewhere"
