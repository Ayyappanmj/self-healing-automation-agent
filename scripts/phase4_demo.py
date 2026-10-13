"""Phase 4 demo: run different broken actions and show how each failure is classified.

Run from the project root:  python -m scripts.phase4_demo
"""
from agent.config import load_settings
from agent.failure_detector import detect_failure
from browser.actions import Action, execute_action
from browser.manager import BrowserManager
from demo_site.app import start_server

SCENARIOS = [
    ("Control: working locator",        "/v1", Action("click", "button#login")),
    ("v1 test on v2 (id -> testid)",    "/v2", Action("click", "button#login")),
    ("v1 test on v3 (names/text)",      "/v3", Action("fill", "#username", "alice")),
    ("v1 test on v4 (hashed classes)",  "/v4", Action("click", "button#login")),
    ("Hidden element",                  "/v1", Action("assert_visible", "#welcome")),
    ("Ambiguous locator",               "/v1", Action("fill", "input", "x")),
    ("Invalid selector",                "/v1", Action("click", "button[")),
]

if __name__ == "__main__":
    s = load_settings()
    server = start_server(port=0)
    base = f"http://127.0.0.1:{server.server_address[1]}"
    with BrowserManager(headless=s.headless, slow_mo=200) as bm:
        for label, route, action in SCENARIOS:
            bm.goto(base + route)
            result = execute_action(bm.page, action, timeout_ms=1500)
            report = detect_failure(bm.page, result, screenshot_dir="screenshots")
            print(f"\n{label}  [{route}]")
            if report is None:
                print("  OK, no failure")
                continue
            print(f"  type      : {report.failure_type.value}")
            print(f"  healable  : {report.healable}")
            print(f"  matched   : {report.matched_count}")
            print(f"  locator   : {report.locator}")
            print(f"  page      : {report.title!r} @ {report.url}")
            print(f"  screenshot: {report.screenshot_path}")
    server.shutdown()
