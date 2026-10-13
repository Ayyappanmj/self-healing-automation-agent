"""Export stored runs to JSON and load them back. Used for the read-only showcase deployment:
a hosted copy that displays a REAL recorded run (exported from a local run) without launching browsers."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from storage.database import Database

FORMAT_VERSION = 1


def export_dict(db: Database) -> dict:
    return {"format": FORMAT_VERSION, "exported_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "results": db.results(), "records": db.records(), "locator_updates": db.locator_updates()}


def export_json(db: Database) -> str:
    return json.dumps(export_dict(db), indent=2, ensure_ascii=False, default=str)


def load_export(db: Database, data: dict) -> dict[str, int]:
    """Import an export into `db`, keeping the original grouping into runs. Returns the imported counts."""
    if not isinstance(data, dict) or "results" not in data or "records" not in data:
        raise ValueError("Not a showcase export: expected a JSON object with 'results' and 'records'.")
    run_ids: dict[int, int] = {}

    def run_for(old: int | None) -> int:
        key = old if old is not None else 0
        if key not in run_ids:
            run_ids[key] = db.start_run("showcase", {"source_run": old})
        return run_ids[key]

    for r in data["results"]:
        db.add_result(run_for(r.get("run_id")), r)
    for rec in data["records"]:
        db.add_record(run_for(rec.get("run_id")), rec)
    for u in data.get("locator_updates", []):
        db.restore_locator_update(u)
    for run in run_ids.values():
        db.finish_run(run, {"imported": True})
    return {"results": len(data["results"]), "records": len(data["records"]),
            "locator_updates": len(data.get("locator_updates", []))}


def load_export_file(db: Database, path: str | Path) -> dict:
    """Load a file and return its header info (exported_at) plus counts. Raises FileNotFoundError / ValueError."""
    p = Path(path)
    data = json.loads(p.read_text(encoding="utf-8"))
    counts = load_export(db, data)
    return {"exported_at": data.get("exported_at", ""), **counts}
