"""Tests for everything behind the Streamlit pages (they need no browser and no Streamlit)."""
import csv
import io
import json
import subprocess
import sys
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from agent.config import Settings
from agent.runner import build_parser, main, run_plan
from agent.scenarios import PlanItem, build_plan
from tests.fakes import FakeManager, FakePage
from ui import runs
from ui import settings as settings_page
from ui.exports import build_markdown_report, records_to_csv, records_to_json
from ui.explain import explain_record, pipeline_steps
from ui.live import LiveLog
from ui.metrics import compute_metrics, fmt_ms, fmt_percent, recent_events
from ui.runs import RunOptions, build_command, build_env, collect_snapshot, stream_events
from ui.state import RunStore


def test_gemini_connection_check_passes_timeout(monkeypatch):
    calls = {}

    class FakeGeminiProvider:
        def __init__(self, api_key, model, timeout_s):
            calls["init"] = (api_key, model, timeout_s)

        def complete(self, system, prompt, timeout_s):
            calls["complete"] = (system, prompt, timeout_s)

    settings = Settings(gemini_api_key="test-key", gemini_model="test-model", llm_timeout_s=17)
    monkeypatch.setattr(settings_page, "load_settings", lambda: settings)
    monkeypatch.setattr(settings_page, "GeminiProvider", FakeGeminiProvider)
    monkeypatch.setattr(settings_page.st, "session_state", SimpleNamespace(get=lambda key, default=None: default))
    monkeypatch.setattr(settings_page.st, "success", lambda message: None)

    settings_page._check_llm("gemini")

    assert calls["init"] == ("test-key", "test-model", 17)
    assert calls["complete"] == ("Reply briefly with OK.", "Connection test", 17)


def fake_run(versions=("v2", "v3"), **plan_kw):
    """Run the real plan against browser-free fakes; return (events, results)."""
    pages = {f"http://demo/{v}": FakePage.version(v) for v in ("v1", "v2", "v3", "v4")}
    events = []
    results = run_plan(FakeManager(pages), Settings(), build_plan(versions, **plan_kw), "http://demo",
                       emit=lambda event, /, **p: events.append({"event": event, **p}), engine=None, screenshot_dir=None)
    return events, results


# --- scenarios & runner -------------------------------------------------------------
def test_build_plan_variants():
    assert [p.kind for p in build_plan()] == ["baseline", "ui_change", "ui_change", "safety"]
    assert [p.version for p in build_plan(("v4",), include_baseline=False, include_safety=False)] == ["v4"]
    assert all(isinstance(p, PlanItem) and p.steps for p in build_plan(("v2", "v3", "v4")))


def test_run_plan_emits_events_in_order():
    events, results = fake_run()
    kinds = [e["event"] for e in events]
    assert kinds[0] == "run_start" and kinds[-1] == "run_end"
    assert kinds.count("test_start") == kinds.count("test_end") == 4
    assert kinds.index("test_start") < kinds.index("step_start") < kinds.index("test_end")
    assert "failure" in kinds and "healing" in kinds
    json.dumps(events, default=str)                       # every event must be serialisable


def test_run_plan_outcomes():
    _, results = fake_run()
    assert [r.status for r in results] == ["passed", "healed", "manual_review", "manual_review"]
    # v3's last step (welcome message) is refused; the safety check refuses the missing target
    assert results[2].steps[-1].status == "manual_review"
    assert results[3].healing_records[0].retry_result == {}


def test_run_end_lists_healed_locators():
    events, _ = fake_run()
    healed = events[-1]["healed_locators"]
    assert {"test": "login flow @v2", "old": "button#login", "new": '[data-testid="login-button"]'} in healed


def test_runner_cli_help_and_bad_versions():
    assert "--versions" in build_parser().format_help()
    out = subprocess.run([sys.executable, "-m", "agent.runner", "--help"], capture_output=True, text=True, cwd=runs.ROOT)
    assert out.returncode == 0 and "--inspect" in out.stdout
    bad = subprocess.run([sys.executable, "-m", "agent.runner", "--versions", "v9"], capture_output=True, text=True, cwd=runs.ROOT)
    assert bad.returncode == 2 and "must be a subset" in bad.stderr


# --- live log --------------------------------------------------------------------------------
def test_live_log_collects_results_records_and_markdown():
    events, _ = fake_run()
    log = LiveLog()
    for e in events:
        log.feed(e)
    md = log.markdown()
    assert len(log.results) == 4 and log.results[0]["kind"] == "baseline"
    assert len(log.records) == sum(1 for e in events if e["event"] == "healing")
    assert "Healed locator successfully." in md and "manual review required" in md
    assert "❌" in md and "✅" in md and "🩹" in md
    assert "`[data-testid=\"login-button\"]`" in md


def test_live_log_error_and_exit_handling():
    log = LiveLog()
    log.feed({"event": "console", "line": "boom detail"})
    log.feed({"event": "exit", "code": 3})
    assert log.finished and "exited with code 3" in log.error and "boom detail" in log.error
    log2 = LiveLog()
    log2.feed({"event": "error", "message": "Error: Executable doesn't exist"})
    log2.feed({"event": "exit", "code": 1})
    assert log2.error.startswith("Error: Executable") and log2.markdown().count("🛑") == 1


def test_live_log_ignores_unknown_events():
    log = LiveLog()
    log.feed({"event": "mystery"})
    log.feed({})
    assert log.lines == []


# --- metrics -------------------------------------------------------------------------------------
def test_metrics_with_no_data_are_none_not_zero_rates():
    m = compute_metrics([], [])
    assert m["total_tests"] == 0 and m["healing_success_rate"] is None and m["avg_healing_ms"] is None
    assert fmt_percent(None) == "—" and fmt_ms(None) == "—"


def test_metrics_computed_from_real_run():
    _, results = fake_run()
    res = [r.to_dict() for r in results]
    recs = [h.to_dict() for r in results for h in r.healing_records]
    m = compute_metrics(res, recs)
    healed = [r for r in recs if r["status"] == "healed"]
    assert m["total_tests"] == 4 and m["successful_tests"] == 2 and m["manual_review_tests"] == 2
    assert m["healed_locators"] == len(healed) and m["healing_attempts"] == len(recs)
    assert m["healing_success_rate"] == pytest.approx(len(healed) / len(recs))
    assert m["avg_healing_ms"] == pytest.approx(sum(r["execution_time_ms"] for r in healed) / len(healed))


def test_metrics_exact_arithmetic():
    recs = [{"status": "healed", "execution_time_ms": 100}, {"status": "healed", "execution_time_ms": 300},
            {"status": "manual_review", "execution_time_ms": 50}, {"status": "validation_failed", "execution_time_ms": 10}]
    m = compute_metrics([{"status": "healed"}, {"status": "failed"}, {"status": "passed"}], recs)
    assert (m["healed_locators"], m["healing_attempts"]) == (2, 4)
    assert m["healing_success_rate"] == 0.5 and m["avg_healing_ms"] == 200
    assert (m["successful_tests"], m["failed_tests"]) == (2, 1)


def test_formatters_and_recent_events():
    assert fmt_percent(0.5) == "50%" and fmt_ms(250) == "250 ms" and fmt_ms(1500) == "1.5 s"
    recs = [{"timestamp": f"2026-10-01T10:00:0{i}"} for i in range(5)]
    assert [r["timestamp"][-1] for r in recent_events(recs, 2)] == ["4", "3"]


# --- explanation --------------------------------------------------------------------------------------
def records_by_status():
    _, results = fake_run()
    by = {}
    for r in results:
        for h in r.healing_records:
            by.setdefault(h.status, h.to_dict())
    return by


def test_pipeline_for_healed_record_is_all_done():
    steps = pipeline_steps(records_by_status()["healed"])
    states = {label: state for label, state, _ in steps}
    assert states["Failure detected"] == states["DOM inspected"] == states["Validated"] == states["Retried"] == "done"
    assert states["AI reasoning"] == "skipped" and states["Confidence gate"] == "done"


def test_pipeline_for_manual_review_stops_at_the_gate():
    states = {label: state for label, state, _ in pipeline_steps(records_by_status()["manual_review"])}
    assert states["Confidence gate"] == "failed" and states["Validated"] == "skipped" and states["Retried"] == "skipped"


def test_pipeline_for_not_healable():
    rec = {"status": "not_healable", "failure_type": "invalid_selector"}
    assert [s for _, s, _ in pipeline_steps(rec)][1:] == ["skipped"] * 6


def test_explanations_mention_the_facts():
    healed, review = records_by_status()["healed"], records_by_status()["manual_review"]
    text = explain_record(healed)
    assert healed["original_locator"] in text and healed["selected_locator"] in text and "validated" in text
    assert "did not act" in explain_record(review)
    assert "not a locator problem" in explain_record({"status": "not_healable", "failure_type": "invalid_selector",
                                                        "action_name": "x", "original_locator": "y"})


# --- exports --------------------------------------------------------------------------------------------
def test_csv_has_header_and_one_row_per_record():
    _, results = fake_run()
    recs = [h.to_dict() for r in results for h in r.healing_records]
    rows = list(csv.DictReader(io.StringIO(records_to_csv(recs))))
    assert len(rows) == len(recs) and rows[0]["original_locator"] == recs[0]["original_locator"]
    assert {"timestamp", "test_name", "confidence", "execution_time_ms", "status"} <= set(rows[0])
    assert json.loads(records_to_json(recs))[0]["status"] == recs[0]["status"]


def test_report_with_no_runs_does_not_invent_numbers():
    text = build_markdown_report([], [], datetime(2026, 10, 1, tzinfo=timezone.utc))
    assert "No runs recorded yet" in text and "%" not in text


def test_report_numbers_match_metrics():
    _, results = fake_run()
    res = [r.to_dict() for r in results]
    recs = [h.to_dict() for r in results for h in r.healing_records]
    text = build_markdown_report(res, recs)
    m = compute_metrics(res, recs)
    assert f"Tests run: {m['total_tests']}" in text and f"healed {m['healed_locators']}" in text
    assert "login flow @v2" in text and "button#login" in text


# --- state ----------------------------------------------------------------------------------------------------
def test_run_store_roundtrip(tmp_path):
    from storage.database import Database
    db = Database(tmp_path / "t.db")
    store = RunStore(db)
    rid = store.add_run([{"name": "t", "status": "passed"}], [{"status": "healed", "test_name": "t"}])
    assert rid == 1 and store.results[0]["run_id"] == 1 and store.records[0]["run_id"] == 1
    assert RunStore(Database(tmp_path / "t.db")).results == store.results       # survives a restart
    store.add_run([{"name": "u", "status": "failed"}], [])
    assert len(store.results) == 2 and store.results[1]["run_id"] == 2
    store.clear()
    assert store.results == [] and store.records == []


# --- subprocess plumbing -----------------------------------------------------------------------------------------
def test_build_command_and_env():
    cmd = build_command(RunOptions(versions=("v2", "v4"), baseline=False, safety=False, headless=True, slow_mo=0))
    assert cmd[1:4] == ["-m", "agent.runner", "--versions"] and "v2,v4" in cmd
    assert "--no-baseline" in cmd and "--no-safety" in cmd and cmd[cmd.index("--headless") + 1] == "true"
    env = build_env({"CONFIDENCE_THRESHOLD": "0.6", "EMPTY": "", "NONE": None})
    assert env["CONFIDENCE_THRESHOLD"] == "0.6" and "EMPTY" not in env and "NONE" not in env
    assert env["PYTHONUTF8"] == "1"


def test_stream_events_parses_json_console_and_exit():
    code = ("import json,sys;print(json.dumps({'event':'a','x':1}),flush=True);print('plain noise',flush=True);"
            "print(json.dumps([1,2]),flush=True);sys.exit(4)")
    events = list(stream_events([sys.executable, "-c", code]))
    assert events[0] == {"event": "a", "x": 1}
    assert events[1] == {"event": "console", "line": "plain noise"}
    assert events[2] == {"event": "console", "line": "[1, 2]"}           # JSON but not an event
    assert events[-1] == {"event": "exit", "code": 4}


def test_collect_snapshot_success_and_failure():
    ok = iter([{"event": "snapshot", "snapshot": {"elements": []}}, {"event": "exit", "code": 0}])
    assert collect_snapshot(ok) == ({"elements": []}, None)
    bad = iter([{"event": "console", "line": "Traceback..."}, {"event": "exit", "code": 1}])
    snap, err = collect_snapshot(bad)
    assert snap is None and "exited with code 1" in err and "Traceback" in err
    err_ev = iter([{"event": "error", "message": "NetworkError"}, {"event": "exit", "code": 1}])
    assert collect_snapshot(err_ev) == (None, "NetworkError")


def test_runner_reports_errors_as_events_not_tracebacks():
    out = subprocess.run([sys.executable, "-m", "agent.runner", "--inspect", "http://127.0.0.1:9/nothing"],
                         capture_output=True, text=True, cwd=runs.ROOT, timeout=120)
    lines = [json.loads(x) for x in out.stdout.splitlines() if x.startswith("{")]
    assert out.returncode == 1 and lines and lines[-1]["event"] == "error" and lines[-1]["message"]


# --- remaining UI-logic branches ------------------------------------------------------------------------------
def test_explain_covers_every_outcome_and_llm_state():
    base = {"action_name": "Click login", "original_locator": "#a", "failure_type": "locator_not_found",
            "candidate_locators": [{"x": 1}], "dom_elements": 9, "selected_locator": "#b", "strategy": "id",
            "confidence": 0.8, "threshold": 0.75}
    ok = {**base, "llm": {"status": "ok", "provider": "ollama", "locator": "#b", "reason": "same button"}, "status": "healed"}
    assert "ollama" in explain_record(ok) and "same button" in explain_record(ok)
    helpless = {**base, "llm": {"status": "timeout"}, "status": "validation_failed"}
    assert "did not help (timeout)" in explain_record(helpless) and "No candidate passed validation" in explain_record(helpless)
    assert "still failed" in explain_record({**base, "status": "retry_failed"})
    assert "No element" in explain_record({**base, "status": "no_candidates", "candidate_locators": []})
    assert "boom" in explain_record({**base, "status": "error", "message": "boom"})
    steps = dict((l, s) for l, s, _ in pipeline_steps({**base, "llm": {"status": "bad_json"}, "status": "manual_review"}))
    assert steps["AI reasoning"] == "failed"
    no_cands = dict((l, s) for l, s, _ in pipeline_steps({**base, "candidate_locators": [], "status": "no_candidates"}))
    assert no_cands["Confidence gate"] == "skipped" and no_cands["Candidates scored"] == "failed"


def test_live_log_shows_skipped_steps_and_warnings():
    log = LiveLog()
    log.feed({"event": "step_done", "outcome": {"name": "s", "status": "skipped", "locator_used": "x", "duration_ms": 0}})
    log.feed({"event": "warning", "message": "Could not save to storage: locked"})
    assert "skipped" in log.markdown() and log.warnings == ["Could not save to storage: locked"]


def test_report_lists_none_when_tests_exist_but_no_healing_happened():
    text = build_markdown_report([{"name": "t", "status": "passed", "duration_ms": 10, "steps": []}], [])
    assert "None." in text


def test_runs_command_flags_and_inspect_command_and_blank_lines():
    assert "--reuse-healed" in build_command(RunOptions(reuse_healed=True))
    assert runs.inspect_command("v2")[-2:] == ["--inspect", "v2"]
    events = list(stream_events([sys.executable, "-c", "print('');print('x')"]))
    assert events[0] == {"event": "console", "line": "x"}            # blank lines are dropped
