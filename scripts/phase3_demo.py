"""Phase 3 demo: run v1-style steps on v1 (passes) and on v2 (fails).

Run from the project root:  python -m scripts.phase3_demo
"""
from agent.config import load_settings
from browser.actions import Action, run_steps
from browser.manager import BrowserManager
from demo_site.app import start_server

V1_STEPS = [
    Action("fill", "#username", "alice", name="Enter username"),
    Action("fill", "#password", "secret", name="Enter password"),
    Action("click", "button#login", name="Click login"),
    Action("get_text", "#welcome", name="Read welcome message"),
]


def run(manager: BrowserManager, url: str, timeout_ms: int) -> None:
    print(f"\n=== {url} ===")
    manager.goto(url)
    for r in run_steps(manager.page, V1_STEPS, timeout_ms):
        status = "PASS" if r.success else "FAIL"
        extra = r.output if r.success and r.output else (r.error or "")
        print(f"[{status}] {r.action.label():<24} {r.duration_ms:>7} ms  {extra}")


if __name__ == "__main__":
    settings = load_settings()
    server = start_server(port=0)
    base = f"http://127.0.0.1:{server.server_address[1]}"
    with BrowserManager(headless=settings.headless, slow_mo=400) as bm:
        run(bm, base + "/v1", settings.action_timeout_ms)
        run(bm, base + "/v2", settings.action_timeout_ms)
    server.shutdown()
