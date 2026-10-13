"""Runs a scenario and prints one JSON event per line. Used by the Streamlit UI (as a subprocess,
so Playwright's browser runs outside Streamlit) and runnable by hand:

    python -m agent.runner --versions v2,v3
    python -m agent.runner --inspect v2
"""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import replace
from typing import Any, Callable

from agent.config import Settings, load_settings
from agent.logging_setup import JsonlLog, format_record_line, setup_logging
from agent.controller import AutomationController, AutomationResult, AutomationTest
from agent.scenarios import UI_CHANGES, PlanItem, build_plan

Emit = Callable[..., None]


def emit_json(event: str, /, **payload: Any) -> None:
    """One JSON object per line. `event` is positional-only so payloads may contain a `kind` field."""
    print(json.dumps({"event": event, **payload}, default=str), flush=True)


def run_plan(manager: Any, settings: Settings, plan: list[PlanItem], base_url: str, emit: Emit = emit_json,
             engine: Any = None, screenshot_dir: str | None = "screenshots", db: Any = None, jsonl: Any = None,
             logger: Any = None, reuse_healed: bool = False) -> list[AutomationResult]:
    """Run a plan, emit events, and (optionally) persist everything. A storage problem never fails the run."""
    warned: set[str] = set()

    def guarded(fn: Callable, *args: Any) -> Any:
        try:
            return fn(*args)
        except Exception as exc:
            msg = f"Could not save to storage: {type(exc).__name__}: {_first_line(exc)}"
            if msg not in warned:
                warned.add(msg)
                emit("warning", message=msg)
            return None

    run_id = guarded(db.start_run, "demo run", {"plan": [p.label for p in plan]}) if db else None

    def on_event(kind: str, payload: dict) -> None:
        if kind == "healing":
            rec = payload["record"]
            if db and run_id is not None:
                guarded(db.add_record, run_id, rec)
                if rec["status"] == "healed":
                    guarded(db.upsert_locator_update, rec["test_name"], rec["original_locator"],
                            rec["selected_locator"], rec["strategy"], rec["confidence"])
            if jsonl:
                guarded(jsonl.append, rec)
            if logger:
                guarded(logger.info, format_record_line(rec))
        emit(kind, **payload)

    controller = AutomationController(manager, settings, engine=engine, screenshot_dir=screenshot_dir, on_event=on_event)
    if reuse_healed and db:
        controller.healed.update(guarded(db.load_locator_map) or {})
    provider = controller.engine.provider
    emit("run_start", run_id=run_id, reuse_healed=reuse_healed,
         plan=[{"label": p.label, "version": p.version, "kind": p.kind} for p in plan],
         llm=f"{provider.name}/{provider.model}" if provider else "none", threshold=settings.confidence_threshold)
    results = []
    for item in plan:
        emit("test_start", name=item.name, label=item.label, kind=item.kind, version=item.version,
             url=f"{base_url}/{item.version}")
        result = controller.run_test(AutomationTest(item.name, f"{base_url}/{item.version}", item.steps, item.suite))
        results.append(result)
        summary = {**result.to_dict(), "label": item.label, "kind": item.kind}
        if db and run_id is not None:
            guarded(db.add_result, run_id, summary)
        if logger:
            guarded(logger.info, f"TEST {result.status.upper()} name={result.name!r} duration_ms={result.duration_ms:.0f} "
                                 f"healed_steps={sum(1 for s in result.steps if s.status == 'healed')}")
        emit("test_end", label=item.label, kind=item.kind, result=result.to_dict())
    healed = [{"test": t, "old": o, "new": n} for (t, o), n in controller.healed.items()]
    if db and run_id is not None:
        guarded(db.finish_run, run_id, {"tests": len(results), "healed_locators": len(healed)})
    emit("run_end", run_id=run_id, healed_locators=healed)
    return results


def _first_line(exc: Exception) -> str:
    text = str(exc).strip()
    return text.splitlines()[0] if text else type(exc).__name__


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="python -m agent.runner", description=__doc__.split("\n\n")[0])
    p.add_argument("--versions", default="v2,v3", help="comma list of changed UI versions: v2,v3,v4")
    p.add_argument("--no-baseline", action="store_true", help="skip the baseline run on UI v1")
    p.add_argument("--no-safety", action="store_true", help="skip the missing-target safety check")
    p.add_argument("--headless", choices=["true", "false"], help="override HEADLESS from .env")
    p.add_argument("--slow-mo", type=int, default=250, help="milliseconds between browser actions")
    p.add_argument("--no-persist", action="store_true", help="do not write to the database or log files")
    p.add_argument("--reuse-healed", action="store_true",
                   help="reuse locators healed in earlier runs (also REUSE_HEALED_LOCATORS=true)")
    p.add_argument("--inspect", metavar="V_OR_URL", help="inspect one page (v1..v4 or a URL) and print its DOM snapshot")
    return p


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    versions = tuple(v for v in args.versions.split(",") if v)
    if any(v not in UI_CHANGES for v in versions):
        parser.error(f"--versions must be a subset of {','.join(UI_CHANGES)}")
    settings = load_settings()
    if args.headless:
        settings = replace(settings, headless=args.headless == "true")
    try:
        from browser.manager import BrowserManager
        from demo_site.app import start_server

        server = start_server(port=0)
        base = f"http://127.0.0.1:{server.server_address[1]}"
        try:
            if args.inspect:
                from agent.dom_inspector import inspect_dom
                url = f"{base}/{args.inspect}" if args.inspect in ("v1", *UI_CHANGES) else args.inspect
                with BrowserManager(headless=True) as bm:
                    bm.goto(url)
                    emit_json("snapshot", url=url, snapshot=inspect_dom(bm.page).to_dict())
                return 0
            plan = build_plan(versions, not args.no_baseline, not args.no_safety)
            db = jsonl = logger = None
            if not args.no_persist:
                try:
                    from storage.database import Database
                    db = Database(settings.db_path)
                    jsonl = JsonlLog(settings.log_dir)
                    logger = setup_logging(settings.log_dir, settings.log_level)
                except Exception as exc:
                    emit_json("warning", message=f"Storage unavailable, results will not be saved: "
                                                 f"{type(exc).__name__}: {_first_line(exc)}")
            with BrowserManager(headless=settings.headless, slow_mo=args.slow_mo) as bm:
                run_plan(bm, settings, plan, base, db=db, jsonl=jsonl, logger=logger,
                         screenshot_dir=settings.screenshot_dir,
                         reuse_healed=args.reuse_healed or settings.reuse_healed)
            return 0
        finally:
            server.shutdown()
    except Exception as exc:                       # missing browser, crash, ...
        emit_json("error", message=f"{type(exc).__name__}: {_first_line(exc)}")
        return 1


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
