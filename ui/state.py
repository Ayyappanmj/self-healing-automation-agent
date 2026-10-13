"""Run store: a thin adapter over the SQLite database (the UI only reads; the runner writes)."""
from __future__ import annotations

from storage.database import Database


class RunStore:
    def __init__(self, db: Database):
        self.db = db

    @property
    def results(self) -> list[dict]:
        return self.db.results()

    @property
    def records(self) -> list[dict]:
        return self.db.records()

    def add_run(self, results: list[dict], records: list[dict]) -> int:
        """Used by tests and seeding. Real runs are written by agent.runner."""
        run_id = self.db.start_run("imported")
        for r in results:
            self.db.add_result(run_id, r)
        for rec in records:
            self.db.add_record(run_id, rec)
        self.db.finish_run(run_id, {"tests": len(results)})
        return run_id

    def clear(self) -> None:
        self.db.clear()
