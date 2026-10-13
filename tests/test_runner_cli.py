"""agent.runner.main() end to end in-process, with a browser-free manager."""
import contextlib
import io
import json

import pytest

from agent import runner
from agent.logging_setup import JSONL_FILE
from storage.database import Database
from tests.fakes import RoutedManager, reset_logging


def invoke(args, monkeypatch, tmp_path, env=None):
    monkeypatch.setattr("browser.manager.BrowserManager", RoutedManager)
    monkeypatch.setenv("DB_PATH", str(tmp_path / "cli.db"))
    monkeypatch.setenv("LOG_DIR", str(tmp_path / "logs"))
    monkeypatch.setenv("SCREENSHOT_DIR", str(tmp_path / "shots"))
    for k, v in (env or {}).items():
        monkeypatch.setenv(k, v)
    RoutedManager.instances.clear()
    RoutedManager.fail_on_enter = None
    out = io.StringIO()
    try:
        with contextlib.redirect_stdout(out):
            code = runner.main(args)
    finally:
        reset_logging()
    events = [json.loads(line) for line in out.getvalue().splitlines() if line.startswith("{")]
    return code, events


def test_run_without_persistence_writes_nothing(monkeypatch, tmp_path):
    code, events = invoke(["--versions", "v2", "--no-persist", "--headless", "true"], monkeypatch, tmp_path)
    kinds = [e["event"] for e in events]
    assert code == 0 and kinds[0] == "run_start" and kinds[-1] == "run_end"
    assert events[0]["run_id"] is None and not (tmp_path / "cli.db").exists() and not (tmp_path / "logs").exists()
    assert RoutedManager.instances[0].headless is True


def test_run_persists_results_and_logs(monkeypatch, tmp_path):
    code, events = invoke(["--versions", "v2,v3"], monkeypatch, tmp_path)
    db = Database(tmp_path / "cli.db")
    assert code == 0 and events[0]["run_id"] == 1
    assert [r["name"] for r in db.results()][:2] == ["login flow @v1", "login flow @v2"]
    assert db.records() and (tmp_path / "logs" / JSONL_FILE).exists()


def test_flags_select_the_plan(monkeypatch, tmp_path):
    _, events = invoke(["--versions", "v4", "--no-baseline", "--no-safety", "--no-persist"], monkeypatch, tmp_path)
    assert [e["version"] for e in events if e["event"] == "test_start"] == ["v4"]


def test_slow_mo_is_passed_to_the_browser_manager(monkeypatch, tmp_path):
    invoke(["--no-persist", "--slow-mo", "0", "--versions", "v2"], monkeypatch, tmp_path)
    assert RoutedManager.instances[0].slow_mo == 0


def test_reuse_healed_flag_skips_known_repairs(monkeypatch, tmp_path):
    invoke(["--versions", "v2"], monkeypatch, tmp_path)
    _, events = invoke(["--versions", "v2", "--reuse-healed"], monkeypatch, tmp_path)
    start = next(e for e in events if e["event"] == "run_start")
    assert start["reuse_healed"] is True
    v2 = [e for e in events if e["event"] == "test_end" and e["result"]["name"] == "login flow @v2"][0]
    assert v2["result"]["healing_count"] <= 1                     # only the refused welcome step may still need healing


def test_reuse_can_also_come_from_the_environment(monkeypatch, tmp_path):
    _, events = invoke(["--versions", "v2", "--no-persist"], monkeypatch, tmp_path, env={"REUSE_HEALED_LOCATORS": "true"})
    assert events[0]["reuse_healed"] is True


def test_inspect_mode_prints_a_snapshot_for_a_demo_page(monkeypatch, tmp_path):
    code, events = invoke(["--inspect", "v2"], monkeypatch, tmp_path)
    snap = [e for e in events if e["event"] == "snapshot"][0]
    assert code == 0 and snap["url"].endswith("/v2") and snap["snapshot"]["elements"]


def test_inspect_mode_accepts_a_custom_url(monkeypatch, tmp_path):
    code, events = invoke(["--inspect", "http://example.test/page"], monkeypatch, tmp_path)
    assert code == 0 and events[-1]["url"] == "http://example.test/page"


def test_browser_failure_becomes_an_error_event_and_exit_code_1(monkeypatch, tmp_path):
    monkeypatch.setattr(RoutedManager, "fail_on_enter", None)
    monkeypatch.setattr("browser.manager.BrowserManager", RoutedManager)
    monkeypatch.setenv("DB_PATH", str(tmp_path / "x.db"))
    monkeypatch.setenv("LOG_DIR", str(tmp_path / "logs"))
    monkeypatch.setenv("SCREENSHOT_DIR", str(tmp_path / "shots"))
    RoutedManager.fail_on_enter = RuntimeError("Executable doesn't exist at C:\\pw\\chrome.exe\nrun playwright install")
    out = io.StringIO()
    try:
        with contextlib.redirect_stdout(out):
            code = runner.main(["--versions", "v2"])
    finally:
        RoutedManager.fail_on_enter = None
        reset_logging()
    last = json.loads(out.getvalue().splitlines()[-1])
    assert code == 1 and last["event"] == "error" and last["message"].startswith("RuntimeError: Executable doesn't exist")
    assert "\n" not in last["message"]


def test_unwritable_storage_warns_and_the_run_still_completes(monkeypatch, tmp_path):
    blocker = tmp_path / "blocker"
    blocker.write_text("i am a file, not a folder")
    code, events = invoke(["--versions", "v2"], monkeypatch, tmp_path, env={"DB_PATH": str(blocker / "sub" / "h.db")})
    warning = [e for e in events if e["event"] == "warning"]
    assert code == 0 and len(warning) == 1 and "will not be saved" in warning[0]["message"]
    assert any(e["event"] == "test_end" for e in events)


def test_unknown_version_is_a_usage_error():
    with pytest.raises(SystemExit) as exc:
        runner.main(["--versions", "v9"])
    assert exc.value.code == 2


def test_first_line_helper():
    assert runner._first_line(ValueError("a\nb")) == "a" and runner._first_line(ValueError()) == "ValueError"


def test_screenshots_go_to_the_configured_folder(monkeypatch, tmp_path):
    invoke(["--versions", "v2", "--no-persist"], monkeypatch, tmp_path)
    assert list((tmp_path / "shots").glob("failure_*.png"))
