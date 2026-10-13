"""SQLite storage: test runs, test results, healing records and the locator repository.

One short-lived connection per operation (thread-safe, and the Streamlit app can read while the
runner subprocess writes, thanks to WAL mode). Records are stored as full JSON plus flat columns
for querying.
"""
from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

ROOT = Path(__file__).resolve().parent.parent
SCHEMA_VERSION = 1

SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    label TEXT NOT NULL DEFAULT '',
    meta_json TEXT NOT NULL DEFAULT '{}'
);
CREATE TABLE IF NOT EXISTS test_results (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id INTEGER NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
    recorded_at TEXT NOT NULL,
    name TEXT NOT NULL,
    status TEXT NOT NULL,
    label TEXT NOT NULL DEFAULT '',
    kind TEXT NOT NULL DEFAULT '',
    duration_ms REAL NOT NULL DEFAULT 0,
    message TEXT NOT NULL DEFAULT '',
    crash_restarts INTEGER NOT NULL DEFAULT 0,
    healing_count INTEGER NOT NULL DEFAULT 0,
    steps_json TEXT NOT NULL DEFAULT '[]'
);
CREATE TABLE IF NOT EXISTS healing_records (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id INTEGER NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
    timestamp TEXT NOT NULL,
    test_name TEXT NOT NULL,
    action TEXT NOT NULL DEFAULT '',
    original_locator TEXT NOT NULL DEFAULT '',
    selected_locator TEXT NOT NULL DEFAULT '',
    strategy TEXT NOT NULL DEFAULT '',
    failure_type TEXT NOT NULL DEFAULT '',
    confidence REAL NOT NULL DEFAULT 0,
    status TEXT NOT NULL,
    execution_time_ms REAL NOT NULL DEFAULT 0,
    record_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS locator_updates (
    test_name TEXT NOT NULL,
    original_locator TEXT NOT NULL,
    new_locator TEXT NOT NULL,
    strategy TEXT NOT NULL DEFAULT '',
    confidence REAL NOT NULL DEFAULT 0,
    first_healed_at TEXT NOT NULL,
    last_healed_at TEXT NOT NULL,
    times_healed INTEGER NOT NULL DEFAULT 1,
    PRIMARY KEY (test_name, original_locator)
);
CREATE INDEX IF NOT EXISTS idx_results_run ON test_results(run_id);
CREATE INDEX IF NOT EXISTS idx_records_run ON healing_records(run_id);
CREATE INDEX IF NOT EXISTS idx_records_status ON healing_records(status);
"""


def project_path(path: str | Path) -> Path:
    """Relative paths are resolved against the project root, whatever the working directory is."""
    p = Path(path)
    return p if p.is_absolute() else ROOT / p


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def _dumps(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False, default=str)


class Database:
    def __init__(self, path: str | Path = "data/healing.db"):
        self.path = project_path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    # ---- plumbing -------------------------------------------------------------
    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, timeout=15)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    @contextmanager
    def _tx(self) -> Iterator[sqlite3.Connection]:
        conn = self._connect()
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def _init_schema(self) -> None:
        with self._tx() as c:
            c.execute("PRAGMA journal_mode = WAL")
            if c.execute("PRAGMA user_version").fetchone()[0] < SCHEMA_VERSION:
                c.executescript(SCHEMA)
                c.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
            else:
                c.executescript(SCHEMA)       # idempotent (IF NOT EXISTS)

    # ---- runs ---------------------------------------------------------------------
    def start_run(self, label: str = "", meta: dict | None = None) -> int:
        with self._tx() as c:
            return c.execute("INSERT INTO runs(started_at, label, meta_json) VALUES (?,?,?)",
                             (_now(), label, _dumps(meta or {}))).lastrowid

    def finish_run(self, run_id: int, meta: dict | None = None) -> None:
        with self._tx() as c:
            c.execute("UPDATE runs SET finished_at = ?, meta_json = ? WHERE id = ?",
                      (_now(), _dumps(meta or {}), run_id))

    def runs(self) -> list[dict]:
        with self._tx() as c:
            return [{**dict(r), "meta": json.loads(r["meta_json"])} for r in c.execute("SELECT * FROM runs ORDER BY id")]

    # ---- results & records ---------------------------------------------------------------
    def add_result(self, run_id: int, result: dict) -> int:
        with self._tx() as c:
            return c.execute(
                "INSERT INTO test_results(run_id, recorded_at, name, status, label, kind, duration_ms, message,"
                " crash_restarts, healing_count, steps_json) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (run_id, _now(), result.get("name", ""), result.get("status", ""), result.get("label", ""),
                 result.get("kind", ""), float(result.get("duration_ms", 0)), result.get("message", ""),
                 int(result.get("crash_restarts", 0)), int(result.get("healing_count", 0)),
                 _dumps(result.get("steps", [])))).lastrowid

    def add_record(self, run_id: int, rec: dict) -> int:
        with self._tx() as c:
            return c.execute(
                "INSERT INTO healing_records(run_id, timestamp, test_name, action, original_locator, selected_locator,"
                " strategy, failure_type, confidence, status, execution_time_ms, record_json) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                (run_id, rec.get("timestamp", _now()), rec.get("test_name", ""), rec.get("action", ""),
                 rec.get("original_locator", ""), rec.get("selected_locator", ""), rec.get("strategy", ""),
                 rec.get("failure_type", ""), float(rec.get("confidence", 0)), rec.get("status", ""),
                 float(rec.get("execution_time_ms", 0)), _dumps(rec))).lastrowid

    def results(self, run_id: int | None = None) -> list[dict]:
        sql, args = "SELECT * FROM test_results", ()
        if run_id is not None:
            sql, args = sql + " WHERE run_id = ?", (run_id,)
        with self._tx() as c:
            rows = c.execute(sql + " ORDER BY id", args).fetchall()
        return [{"id": r["id"], "run_id": r["run_id"], "recorded_at": r["recorded_at"], "name": r["name"],
                 "status": r["status"], "label": r["label"], "kind": r["kind"], "duration_ms": r["duration_ms"],
                 "message": r["message"], "crash_restarts": r["crash_restarts"], "healing_count": r["healing_count"],
                 "steps": json.loads(r["steps_json"])} for r in rows]

    def records(self, run_id: int | None = None) -> list[dict]:
        sql, args = "SELECT id, run_id, record_json FROM healing_records", ()
        if run_id is not None:
            sql, args = sql + " WHERE run_id = ?", (run_id,)
        with self._tx() as c:
            rows = c.execute(sql + " ORDER BY id", args).fetchall()
        return [{**json.loads(r["record_json"]), "id": r["id"], "run_id": r["run_id"]} for r in rows]

    # ---- locator repository ---------------------------------------------------------------------
    def upsert_locator_update(self, test_name: str, original: str, new: str, strategy: str = "",
                              confidence: float = 0.0) -> None:
        now = _now()
        with self._tx() as c:
            c.execute(
                "INSERT INTO locator_updates(test_name, original_locator, new_locator, strategy, confidence,"
                " first_healed_at, last_healed_at, times_healed) VALUES (?,?,?,?,?,?,?,1)"
                " ON CONFLICT(test_name, original_locator) DO UPDATE SET new_locator = excluded.new_locator,"
                " strategy = excluded.strategy, confidence = excluded.confidence,"
                " last_healed_at = excluded.last_healed_at, times_healed = times_healed + 1",
                (test_name, original, new, strategy, confidence, now, now))

    def restore_locator_update(self, row: dict) -> None:
        """Insert a repository row exactly as exported (keeps dates and the times-healed count)."""
        with self._tx() as c:
            c.execute(
                "INSERT OR REPLACE INTO locator_updates(test_name, original_locator, new_locator, strategy, confidence,"
                " first_healed_at, last_healed_at, times_healed) VALUES (?,?,?,?,?,?,?,?)",
                (row["test_name"], row["original_locator"], row["new_locator"], row.get("strategy", ""),
                 float(row.get("confidence", 0)), row.get("first_healed_at") or _now(),
                 row.get("last_healed_at") or _now(), int(row.get("times_healed", 1))))

    def locator_updates(self) -> list[dict]:
        with self._tx() as c:
            return [dict(r) for r in c.execute("SELECT * FROM locator_updates ORDER BY last_healed_at DESC")]

    def load_locator_map(self) -> dict[tuple[str, str], str]:
        return {(u["test_name"], u["original_locator"]): u["new_locator"] for u in self.locator_updates()}

    # ---- housekeeping ------------------------------------------------------------------------------
    def counts(self) -> dict[str, int]:
        with self._tx() as c:
            return {t: c.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
                    for t in ("runs", "test_results", "healing_records", "locator_updates")}

    def size_bytes(self) -> int:
        return self.path.stat().st_size if self.path.exists() else 0

    def clear(self) -> None:
        with self._tx() as c:
            for table in ("healing_records", "test_results", "locator_updates", "runs"):
                c.execute(f"DELETE FROM {table}")
