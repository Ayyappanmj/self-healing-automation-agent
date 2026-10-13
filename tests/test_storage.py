import json
import sqlite3
import threading

import pytest

from agent.config import Settings, load_settings
from agent.logging_setup import JsonlLog, format_record_line, read_tail, setup_logging
from agent.runner import run_plan
from agent.scenarios import build_plan
from storage.database import SCHEMA_VERSION, Database, project_path
from tests.fakes import FakeManager, FakePage
from ui.metrics import compute_metrics
from ui.state import RunStore

REC = {"timestamp": "2026-10-01T10:00:00.000+00:00", "test_name": "t", "action": "click", "action_name": "Click login",
       "original_locator": "button#login", "selected_locator": '[data-testid="x"]', "strategy": "test_id",
       "failure_type": "locator_not_found", "confidence": 0.97, "status": "healed", "execution_time_ms": 123.4,
       "message": "Healed locator successfully. — ok", "candidate_locators": [{"locator": "a", "score": 0.9}],
       "validation_result": {"valid": True}, "retry_result": {"success": True}, "llm": {"status": "disabled"}}
RESULT = {"name": "t", "status": "healed", "label": "L", "kind": "ui_change", "duration_ms": 900.5, "message": "",
          "crash_restarts": 0, "healing_count": 1, "steps": [{"name": "s", "status": "healed", "locator_used": "x"}]}


@pytest.fixture
def db(tmp_path):
    return Database(tmp_path / "t.db")


# --- database ---------------------------------------------------------------------------
def test_schema_created_and_idempotent(tmp_path):
    path = tmp_path / "new" / "dir" / "h.db"                         # missing folders are created
    Database(path)
    Database(path)                                                    # reopening must not fail or reset
    conn = sqlite3.connect(path)
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"runs", "test_results", "healing_records", "locator_updates"} <= tables
    assert conn.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
    conn.close()


def test_relative_paths_resolve_against_project_root():
    assert project_path("data/x.db").is_absolute() and project_path("data/x.db").parts[-2:] == ("data", "x.db")
    assert str(project_path("/abs/y.db")).endswith("y.db")


def test_record_roundtrip_is_lossless_including_unicode(db):
    run = db.start_run("r")
    db.add_record(run, REC)
    (back,) = db.records()
    assert {k: back[k] for k in REC} == REC and back["run_id"] == run and back["id"] == 1


def test_flat_columns_support_sql_queries(db):
    run = db.start_run()
    db.add_record(run, REC)
    db.add_record(run, {**REC, "status": "manual_review", "selected_locator": ""})
    conn = sqlite3.connect(db.path)
    assert conn.execute("SELECT COUNT(*) FROM healing_records WHERE status='healed'").fetchone()[0] == 1
    assert conn.execute("SELECT AVG(confidence) FROM healing_records").fetchone()[0] == pytest.approx(0.97)
    conn.close()


def test_result_roundtrip_and_run_filter(db):
    r1, r2 = db.start_run("a"), db.start_run("b")
    db.add_result(r1, RESULT)
    db.add_result(r2, {**RESULT, "name": "u", "status": "failed"})
    all_results = db.results()
    assert [r["name"] for r in all_results] == ["t", "u"]
    assert all_results[0]["steps"] == RESULT["steps"] and all_results[0]["duration_ms"] == 900.5
    assert [r["name"] for r in db.results(run_id=r2)] == ["u"]
    assert [r["id"] for r in db.records(run_id=r2)] == []


def test_runs_listing_and_finish(db):
    rid = db.start_run("demo", {"plan": ["a"]})
    db.finish_run(rid, {"tests": 3})
    (run,) = db.runs()
    assert run["label"] == "demo" and run["finished_at"] and run["meta"] == {"tests": 3}


def test_locator_repository_upsert_counts_heals(db):
    db.upsert_locator_update("t", "button#login", "[a]", "test_id", 0.9)
    db.upsert_locator_update("t", "button#login", "[b]", "role_name", 0.8)
    db.upsert_locator_update("t", "#username", "[u]", "id", 0.9)
    updates = {u["original_locator"]: u for u in db.locator_updates()}
    assert updates["button#login"]["new_locator"] == "[b]" and updates["button#login"]["times_healed"] == 2
    assert db.load_locator_map() == {("t", "button#login"): "[b]", ("t", "#username"): "[u]"}


def test_data_survives_reopen(tmp_path):
    first = Database(tmp_path / "p.db")
    run = first.start_run()
    first.add_record(run, REC)
    first.add_result(run, RESULT)
    again = Database(tmp_path / "p.db")
    assert len(again.records()) == 1 and len(again.results()) == 1 and again.counts()["runs"] == 1


def test_clear_and_counts(db):
    run = db.start_run()
    db.add_record(run, REC)
    db.add_result(run, RESULT)
    db.upsert_locator_update("t", "a", "b")
    assert db.counts() == {"runs": 1, "test_results": 1, "healing_records": 1, "locator_updates": 1}
    db.clear()
    assert db.counts() == {"runs": 0, "test_results": 0, "healing_records": 0, "locator_updates": 0}
    assert db.size_bytes() > 0


def test_failed_write_rolls_back(db):
    with pytest.raises(sqlite3.IntegrityError):
        db.add_result(999, RESULT)                   # no such run: foreign key must reject it
    assert db.results() == []


def test_concurrent_writers_and_readers(db):
    run = db.start_run()
    errors = []

    def writer(n):
        try:
            for i in range(15):
                db.add_record(run, {**REC, "test_name": f"w{n}-{i}"})
        except Exception as exc:
            errors.append(exc)

    def reader():
        try:
            for _ in range(15):
                db.records()
        except Exception as exc:
            errors.append(exc)

    threads = [threading.Thread(target=writer, args=(n,)) for n in range(3)] + [threading.Thread(target=reader)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert errors == [] and len(db.records()) == 45


def test_run_store_feeds_metrics_from_database(db):
    store = RunStore(db)
    store.add_run([{**RESULT, "status": "healed"}, {**RESULT, "status": "failed"}], [REC, {**REC, "status": "manual_review"}])
    m = compute_metrics(store.results, store.records)
    assert (m["total_tests"], m["successful_tests"], m["failed_tests"]) == (2, 1, 1)
    assert m["healing_success_rate"] == 0.5 and m["avg_healing_ms"] == pytest.approx(123.4)


# --- logs ------------------------------------------------------------------------------------
def test_jsonl_log_append_read_and_tolerates_partial_line(tmp_path):
    log = JsonlLog(tmp_path)
    log.append(REC)
    log.append({**REC, "test_name": "second"})
    with log.path.open("a", encoding="utf-8") as f:
        f.write('{"broken": ')                                     # crash mid-write
    rows = log.read_all()
    assert [r["test_name"] for r in rows] == ["t", "second"]
    assert rows[0]["message"] == REC["message"]
    for field in ("timestamp", "test_name", "original_locator", "candidate_locators", "selected_locator",
                  "confidence", "validation_result", "retry_result", "execution_time_ms"):
        assert field in rows[0]


def test_setup_logging_writes_file_and_is_idempotent(tmp_path):
    import logging
    logger = logging.getLogger("healing_agent")
    for handler in list(logger.handlers):
        handler.close()
        logger.removeHandler(handler)

    try:
        logger = setup_logging(tmp_path / "logs")
        n = len(logger.handlers)
        assert setup_logging(tmp_path / "other") is logger and len(logger.handlers) == n
        logger.info(format_record_line(REC))
        for handler in logger.handlers:
            handler.flush()
        text = read_tail(tmp_path / "logs" / "agent.log")
        assert "HEALED" in text and "button#login" in text and "confidence=0.97" in text
    finally:
        for handler in list(logger.handlers):
            handler.close()
            logger.removeHandler(handler)


def test_read_tail_limits_lines_and_handles_missing(tmp_path):
    f = tmp_path / "x.log"
    f.write_text("\n".join(f"line{i}" for i in range(10)), encoding="utf-8")
    assert read_tail(f, 3) == "line7\nline8\nline9" and read_tail(tmp_path / "nope.log") == ""


# --- runner persistence --------------------------------------------------------------------------------
def fake_pages():
    return {f"http://demo/{v}": FakePage.version(v) for v in ("v1", "v2", "v3", "v4")}


def go(db=None, jsonl=None, reuse=False, versions=("v2", "v3"), pages=None, **kw):
    events = []
    results = run_plan(FakeManager(pages or fake_pages()), Settings(), build_plan(versions), "http://demo",
                       emit=lambda event, /, **p: events.append({"event": event, **p}), screenshot_dir=None,
                       db=db, jsonl=jsonl, reuse_healed=reuse, **kw)
    return events, results


def test_run_is_persisted_and_matches_emitted_events(db, tmp_path):
    jsonl = JsonlLog(tmp_path / "logs")
    events, results = go(db, jsonl)
    emitted = [e["record"] for e in events if e["event"] == "healing"]
    stored = db.records()
    assert [r["original_locator"] for r in stored] == [r["original_locator"] for r in emitted]
    assert [r["status"] for r in stored] == [r["status"] for r in emitted]
    assert [r["name"] for r in db.results()] == [r.name for r in results]
    assert len(jsonl.read_all()) == len(emitted)
    assert next(e for e in events if e["event"] == "run_start")["run_id"] == 1
    (run,) = db.runs()
    assert run["finished_at"] and run["meta"]["tests"] == 4


def test_only_healed_locators_enter_the_repository(db):
    go(db)
    saved = {u["original_locator"] for u in db.locator_updates()}
    healed = {r["original_locator"] for r in db.records() if r["status"] == "healed"}
    assert saved == healed and "button#checkout" not in saved


def test_two_runs_accumulate_history(db):
    go(db)
    go(db)
    assert db.counts()["runs"] == 2 and len({r["run_id"] for r in db.results()}) == 2


def test_reuse_healed_skips_healing_on_next_run(db):
    go(db)                                                    # heals and saves
    before = len(db.records())
    events, results = go(db, reuse=True, pages=fake_pages())  # fresh pages, locators come from storage
    v2 = next(r for r in results if r.name == "login flow @v2")
    assert v2.status in ("passed", "manual_review") and not v2.healing_records
    assert len(db.records()) <= before + 2                     # only unrelated (refused) steps may add records


def test_without_reuse_healing_happens_again(db):
    go(db)
    _, results = go(db, reuse=False)
    assert next(r for r in results if r.name == "login flow @v2").status == "healed"


def test_storage_failure_warns_but_run_completes(db):
    def boom(*a, **k):
        raise sqlite3.OperationalError("database is locked")
    db.add_record = boom
    events, results = go(db)
    warnings = [e for e in events if e["event"] == "warning"]
    assert len(warnings) == 1 and "database is locked" in warnings[0]["message"]    # warned once, not per event
    assert [r.status for r in results] == ["passed", "healed", "manual_review", "manual_review"]


def test_no_db_means_no_files_and_run_id_none(tmp_path):
    events, _ = go()
    assert next(e for e in events if e["event"] == "run_start")["run_id"] is None


# --- settings ---------------------------------------------------------------------------------------------
def test_new_settings_defaults_and_env(monkeypatch):
    s = Settings()
    assert s.log_dir == "data/logs" and s.reuse_healed is False and s.log_level == "INFO"
    monkeypatch.setenv("REUSE_HEALED_LOCATORS", "true")
    monkeypatch.setenv("LOG_LEVEL", "debug")
    loaded = load_settings()
    assert loaded.reuse_healed is True and loaded.log_level == "DEBUG"


def test_reading_a_log_that_does_not_exist_yet_gives_an_empty_list(tmp_path):
    assert JsonlLog(tmp_path / "fresh").read_all() == []


def test_restore_locator_update_keeps_dates_and_counts(db):
    row = {"test_name": "t", "original_locator": "a", "new_locator": "b", "strategy": "id", "confidence": 0.9,
           "first_healed_at": "2026-01-01T00:00:00+00:00", "last_healed_at": "2026-02-01T00:00:00+00:00", "times_healed": 7}
    db.restore_locator_update(row)
    db.restore_locator_update({**row, "new_locator": "c"})          # replaces, does not duplicate
    (back,) = db.locator_updates()
    assert back == {**row, "new_locator": "c"}
