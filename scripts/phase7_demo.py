"""Phase 7 demo: rank candidate locators for several broken steps and decide heal vs manual review.

Run from the project root:  python -m scripts.phase7_demo
"""
from agent.candidate_scorer import LOW_CONFIDENCE_MESSAGE, build_profile, score_candidates
from agent.config import load_settings
from agent.dom_inspector import inspect_dom
from agent.locator_generator import generate_candidates
from browser.actions import Action
from browser.manager import BrowserManager
from demo_site.app import start_server

STEPS = [
    Action("click", "button#login", name="Click login"),
    Action("fill", "#username", "alice", name="Enter username"),
    Action("fill", "#password", "secret", name="Enter password"),
    Action("click", "a#forgot", name="Click forgot password"),
    Action("get_text", "#welcome", name="Read welcome message"),
    Action("click", "button#checkout", name="Click checkout"),   # does not exist: should be refused
]

if __name__ == "__main__":
    s = load_settings()
    server = start_server(port=0)
    base = f"http://127.0.0.1:{server.server_address[1]}"
    with BrowserManager(headless=True) as bm:
        for route in ("/v2", "/v3", "/v4"):
            bm.goto(base + route)
            snap = inspect_dom(bm.page)
            print(f"\n=== {route} (threshold {s.confidence_threshold}) ===")
            for step in STEPS:
                cands = generate_candidates(snap, step.type)
                result = score_candidates(cands, build_profile(step.locator, step.type, step.name))
                best = result.best
                decision = result.decision(s.confidence_threshold)
                print(f"{step.name:<24} {step.locator:<18} -> conf {result.confidence:.2f}  {decision.upper()}")
                if best:
                    print(f"    best: {best.candidate.locator}   ({result.reason()})")
                if decision == "manual_review":
                    print(f"    {LOW_CONFIDENCE_MESSAGE}")
    server.shutdown()
