"""Phase 5 demo: after a v1 locator fails, inspect the changed DOM.

Run from the project root:  python -m scripts.phase5_demo
Writes JSON snapshots to data/snapshot_<version>.json
"""
import json
from pathlib import Path

from agent.config import load_settings
from agent.dom_inspector import inspect_dom
from agent.failure_detector import detect_failure
from browser.actions import Action, execute_action
from browser.manager import BrowserManager
from demo_site.app import start_server

if __name__ == "__main__":
    s = load_settings()
    server = start_server(port=0)
    base = f"http://127.0.0.1:{server.server_address[1]}"
    Path("data").mkdir(exist_ok=True)
    with BrowserManager(headless=s.headless, slow_mo=200) as bm:
        for route in ("/v2", "/v3", "/v4"):
            bm.goto(base + route)
            result = execute_action(bm.page, Action("click", "button#login"), timeout_ms=1200)
            report = detect_failure(bm.page, result)
            print(f"\n=== {route}: {report.summary() if report else 'no failure'} ===")
            snap = inspect_dom(bm.page)
            print(f"{snap.total_elements} relevant elements | visibility from {snap.visibility_source}")
            print(snap.compact(limit=14))
            out = Path("data") / f"snapshot_{route.strip('/')}.json"
            out.write_text(json.dumps(snap.to_dict(), indent=2), encoding="utf-8")
            print(f"saved {out}")
    server.shutdown()
