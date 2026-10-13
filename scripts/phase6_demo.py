"""Phase 6 demo: after a v1 locator fails, generate candidate locators from the new DOM.

Run from the project root:  python -m scripts.phase6_demo
"""
from collections import Counter

from agent.config import load_settings
from agent.dom_inspector import inspect_dom
from agent.failure_detector import detect_failure
from agent.locator_generator import generate_candidates
from browser.actions import Action, execute_action
from browser.manager import BrowserManager
from demo_site.app import start_server

if __name__ == "__main__":
    s = load_settings()
    server = start_server(port=0)
    base = f"http://127.0.0.1:{server.server_address[1]}"
    with BrowserManager(headless=s.headless, slow_mo=200) as bm:
        for route in ("/v2", "/v3", "/v4"):
            bm.goto(base + route)
            action = Action("click", "button#login", name="Click login")
            report = detect_failure(bm.page, execute_action(bm.page, action, timeout_ms=1200))
            print(f"\n=== {route}: {report.summary()} ===")
            cands = generate_candidates(inspect_dom(bm.page), action_type=action.type)
            print(f"{len(cands)} click-compatible candidates:",
                  dict(Counter(c.strategy for c in cands)))
            for c in [c for c in cands if c.element.tag == "button"]:
                ok = bm.page.locator(c.locator).count()
                print(f"  {c.base_priority:.2f} {c.strategy:<10} matches={ok}  {c.locator}")
    server.shutdown()
