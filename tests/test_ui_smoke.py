"""Starts the real Streamlit app in-process (skipped if Streamlit isn't installed)."""
import os

import pytest

pytest.importorskip("streamlit")
from streamlit.testing.v1 import AppTest  # noqa: E402

ROOT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
APP_PATH = os.path.join(ROOT_DIR, "app.py")


def test_app_starts_without_errors_and_shows_empty_state(tmp_path, monkeypatch):
    monkeypatch.setenv("DB_PATH", str(tmp_path / "empty.db"))
    monkeypatch.setenv("LOG_DIR", str(tmp_path / "logs"))
    at = AppTest.from_file(APP_PATH, default_timeout=60).run()
    assert not at.exception
    assert [t.label for t in at.tabs] == [
        "Dashboard", "Run Automation", "Self-Healing", "DOM Inspector", "Healing History",
        "Healing events", "Locator repository", "Logs", "Test Reports", "Settings",
    ]
    values = {m.label: m.value for m in at.metric}
    assert values["Total tests"] == "0" and values["Healing success rate"] == "—"


def test_dashboard_shows_numbers_computed_from_stored_runs(tmp_path, monkeypatch):
    from agent.config import Settings
    from agent.runner import run_plan
    from agent.scenarios import build_plan
    from storage.database import Database
    from tests.fakes import FakeManager, FakePage

    path = tmp_path / "seeded.db"
    monkeypatch.setenv("DB_PATH", str(path))
    monkeypatch.setenv("LOG_DIR", str(tmp_path / "logs"))
    pages = {f"http://demo/{v}": FakePage.version(v) for v in ("v1", "v2", "v3", "v4")}
    events = []
    run_plan(FakeManager(pages), Settings(), build_plan(), "http://demo",
             emit=lambda event, /, **p: events.append({"event": event, **p}), screenshot_dir=None, db=Database(path))
    records = [e["record"] for e in events if e["event"] == "healing"]

    at = AppTest.from_file(APP_PATH, default_timeout=60).run()
    assert not at.exception
    values = {m.label: m.value for m in at.metric}
    assert values["Total tests"] == "4" and values["Successful"] == "2"
    assert values["Healed locators"] == str(sum(1 for r in records if r["status"] == "healed"))
