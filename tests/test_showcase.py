import json

import pytest

from agent.config import Settings, load_settings
from agent.runner import run_plan
from agent.scenarios import build_plan
from storage.database import Database
from storage.showcase import export_dict, export_json, load_export, load_export_file
from tests.fakes import FakeManager, FakePage
from ui.metrics import compute_metrics


def real_run(db):
    pages = {f"http://demo/{v}": FakePage.version(v) for v in ("v1", "v2", "v3", "v4")}
    run_plan(FakeManager(pages), Settings(), build_plan(), "http://demo", emit=lambda e, /, **p: None, screenshot_dir=None, db=db)
    run_plan(FakeManager(pages), Settings(), build_plan(("v4",), include_safety=False), "http://demo",
             emit=lambda e, /, **p: None, screenshot_dir=None, db=db)


def test_export_import_roundtrip_preserves_everything(tmp_path):
    src = Database(tmp_path / "a.db")
    real_run(src)
    dst = Database(tmp_path / "b.db")
    counts = load_export(dst, json.loads(export_json(src)))
    assert counts["results"] == len(src.results()) and counts["records"] == len(src.records())
    strip = lambda rows: [{k: v for k, v in r.items() if k not in ("id", "run_id", "recorded_at")} for r in rows]
    assert strip(dst.results()) == strip(src.results()) and strip(dst.records()) == strip(src.records())
    by_key = lambda db: {(u["test_name"], u["original_locator"]): u for u in db.locator_updates()}
    assert by_key(dst) == by_key(src) and len(by_key(src)) > 3          # full rows, including dates and times_healed


def test_metrics_are_identical_before_and_after_the_roundtrip(tmp_path):
    src, dst = Database(tmp_path / "a.db"), Database(tmp_path / "b.db")
    real_run(src)
    load_export(dst, export_dict(src))
    assert compute_metrics(dst.results(), dst.records()) == compute_metrics(src.results(), src.records())


def test_original_run_grouping_is_kept(tmp_path):
    src, dst = Database(tmp_path / "a.db"), Database(tmp_path / "b.db")
    real_run(src)
    assert len({r["run_id"] for r in src.results()}) == 2
    load_export(dst, export_dict(src))
    groups = lambda db: sorted(sum(1 for r in db.results() if r["run_id"] == rid) for rid in {r["run_id"] for r in db.results()})
    assert groups(dst) == groups(src)


def test_export_header_and_format(tmp_path):
    db = Database(tmp_path / "a.db")
    data = export_dict(db)
    assert data["format"] == 1 and data["exported_at"].endswith("+00:00") and data["results"] == []


def test_invalid_files_give_clear_errors(tmp_path):
    db = Database(tmp_path / "a.db")
    with pytest.raises(ValueError, match="Not a showcase export"):
        load_export(db, {"hello": "world"})
    with pytest.raises(ValueError, match="Not a showcase export"):
        load_export(db, [1, 2, 3])
    bad = tmp_path / "bad.json"
    bad.write_text("{not json", encoding="utf-8")
    with pytest.raises(ValueError):                         # json.JSONDecodeError is a ValueError
        load_export_file(db, bad)
    with pytest.raises(FileNotFoundError):
        load_export_file(db, tmp_path / "missing.json")


def test_load_export_file_reports_counts_and_date(tmp_path):
    src = Database(tmp_path / "a.db")
    real_run(src)
    f = tmp_path / "showcase_run.json"
    f.write_text(export_json(src), encoding="utf-8")
    info = load_export_file(Database(tmp_path / "b.db"), f)
    assert info["results"] == len(src.results()) and info["records"] == len(src.records()) and info["exported_at"]


def test_import_tolerates_records_without_optional_fields(tmp_path):
    db = Database(tmp_path / "a.db")
    counts = load_export(db, {"results": [{"name": "t", "status": "passed"}], "records": [{"status": "healed", "test_name": "t"}]})
    assert counts == {"results": 1, "records": 1, "locator_updates": 0} and db.counts()["runs"] == 1


def test_showcase_settings_defaults_and_env(monkeypatch):
    assert Settings().showcase_mode is False and Settings().showcase_file == "showcase/showcase_run.json"
    monkeypatch.setenv("SHOWCASE_MODE", "true")
    monkeypatch.setenv("SHOWCASE_FILE", "x/y.json")
    s = load_settings()
    assert s.showcase_mode is True and s.showcase_file == "x/y.json"
