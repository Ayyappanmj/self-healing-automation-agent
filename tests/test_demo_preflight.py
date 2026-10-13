import pytest

from agent.config import Settings
from scripts import demo_preflight as pf
from storage.database import Database


class Resp:
    def __init__(self, models): self._m = models
    def raise_for_status(self): pass
    def json(self): return {"models": [{"name": n} for n in self._m]}


def test_python_version_check():
    assert pf.check_python((3, 12)).status == pf.OK
    assert pf.check_python((3, 10)).status == pf.FAIL


def test_missing_packages_are_listed_with_the_fix():
    c = pf.check_packages(lambda name: None if name == "streamlit" else object())
    assert c.status == pf.FAIL and "streamlit" in c.detail and "pip install -r requirements.txt" in c.detail
    assert pf.check_packages(lambda name: object()).status == pf.OK


def test_firefox_failure_tells_you_how_to_install_it():
    def boom():
        raise RuntimeError("Executable doesn't exist at C:\\x\nrun playwright install")
    c = pf.check_firefox(boom)
    assert c.status == pf.FAIL and "playwright install firefox" in c.detail and "\n" not in c.detail
    assert pf.check_firefox(lambda: None).status == pf.OK


def test_headless_mode_fails_because_nothing_can_be_recorded():
    assert pf.check_window_visible(Settings(headless=True)).status == pf.FAIL
    assert pf.check_window_visible(Settings(headless=False)).status == pf.OK


def test_non_default_threshold_is_flagged_for_the_caption():
    assert pf.check_threshold(Settings(confidence_threshold=0.6)).status == pf.WARN
    assert pf.check_threshold(Settings()).status == pf.OK


def test_llm_disabled_reminds_you_not_to_claim_ai_reasoning():
    c = pf.check_llm(Settings(llm_provider="none"))
    assert c.status == pf.INFO and "deterministic" in c.detail and "Do not describe" in c.detail


def test_ollama_unreachable_warns():
    def down(*a, **k):
        raise ConnectionError("refused")
    c = pf.check_llm(Settings(llm_provider="ollama"), get=down)
    assert c.status == pf.WARN and "not reachable" in c.detail


def test_ollama_model_present_or_missing():
    ok = pf.check_llm(Settings(llm_provider="ollama", ollama_model="llama3.2"), get=lambda *a, **k: Resp(["llama3.2:latest"]))
    missing = pf.check_llm(Settings(llm_provider="ollama", ollama_model="llama3.2"), get=lambda *a, **k: Resp(["mistral:7b"]))
    assert ok.status == pf.OK and missing.status == pf.WARN and "ollama pull llama3.2" in missing.detail


def test_gemini_provider_warns_when_key_is_missing():
    c = pf.check_llm(Settings(llm_provider="gemini"))
    assert c.status == pf.WARN and "GEMINI_API_KEY" in c.detail


def test_storage_check_distinguishes_empty_and_used(tmp_path):
    path = tmp_path / "h.db"
    s = Settings(db_path=str(path))
    assert pf.check_storage(s).status == pf.OK                    # no file yet
    db = Database(path)
    assert pf.check_storage(s).status == pf.OK                    # exists but empty
    db.add_record(db.start_run(), {"status": "healed", "test_name": "t"})
    c = pf.check_storage(s)
    assert c.status == pf.WARN and "--reset" in c.detail


def test_port_check():
    class Sock:
        def close(self): pass
    assert pf.check_port(8501, connect=lambda *a, **k: Sock()).status == pf.WARN

    def refused(*a, **k):
        raise ConnectionRefusedError()
    assert pf.check_port(8501, connect=refused).status == pf.OK


def test_reset_requires_confirmation(tmp_path):
    with pytest.raises(PermissionError):
        pf.reset_files(Settings(db_path=str(tmp_path / "x.db")), confirm=False)


def test_reset_removes_only_the_known_demo_files(tmp_path):
    db = tmp_path / "data" / "h.db"
    logs, shots = tmp_path / "logs", tmp_path / "shots"
    Database(db)
    logs.mkdir()
    shots.mkdir()
    keep = [tmp_path / "data" / "notes.txt", logs / "other.txt", shots / "my_screenshot.png", tmp_path / "README.md"]
    delete = [logs / "agent.log", logs / "agent.log.1", logs / "healing.jsonl", shots / "failure_20261001_1.png"]
    for f in keep + delete:
        f.write_text("x")
    removed = pf.reset_files(Settings(db_path=str(db), log_dir=str(logs), screenshot_dir=str(shots)), confirm=True)
    assert not db.exists() and all(not f.exists() for f in delete) and all(f.exists() for f in keep)
    assert str(db) in removed and len(removed) >= 5


def test_reset_on_a_clean_project_is_harmless(tmp_path):
    s = Settings(db_path=str(tmp_path / "none.db"), log_dir=str(tmp_path / "nologs"), screenshot_dir=str(tmp_path / "noshots"))
    assert pf.reset_files(s, confirm=True) == []


def test_main_exit_code_and_output(monkeypatch):
    import io
    import contextlib
    monkeypatch.setattr(pf, "run_checks", lambda s: [pf.Check("A", pf.OK, "fine"), pf.Check("B", pf.WARN, "hm")])
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        assert pf.main([]) == 0
    assert "[ OK ] A: fine" in out.getvalue() and "Ready to record" in out.getvalue()
    monkeypatch.setattr(pf, "run_checks", lambda s: [pf.Check("C", pf.FAIL, "broken")])
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        assert pf.main([]) == 1
    assert "[FAIL] C: broken" in out.getvalue() and "Fix the [FAIL] items" in out.getvalue()


def test_main_reset_cancelled_without_the_word_delete(monkeypatch, tmp_path):
    import io
    import contextlib
    monkeypatch.setenv("DB_PATH", str(tmp_path / "keep.db"))
    Database(tmp_path / "keep.db")
    monkeypatch.setattr(pf, "run_checks", lambda s: [])
    monkeypatch.setattr("builtins.input", lambda prompt="": "no")
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        pf.main(["--reset"])
    assert "Reset cancelled" in out.getvalue() and (tmp_path / "keep.db").exists()


def test_main_reset_with_yes_deletes(monkeypatch, tmp_path):
    import io
    import contextlib
    monkeypatch.setenv("DB_PATH", str(tmp_path / "gone.db"))
    monkeypatch.setenv("LOG_DIR", str(tmp_path / "l"))
    monkeypatch.setenv("SCREENSHOT_DIR", str(tmp_path / "s"))
    Database(tmp_path / "gone.db")
    monkeypatch.setattr(pf, "run_checks", lambda s: [])
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        pf.main(["--reset", "--yes"])
    assert not (tmp_path / "gone.db").exists() and "Removed" in out.getvalue()
