"""Phase 9 demo: the full self-healing loop in a real browser.

Run from the project root:  python -m scripts.phase9_demo
(Set HEADLESS=true in .env to hide the browser window. Set LLM_PROVIDER=ollama to enable the LLM.)

1. Run a login test on UI v1 (passes, and the agent learns what each element looks like).
2. Run the SAME test, still using v1 locators, on v2, v3 and v4 (the UI changed).
3. Try a step whose target does not exist: the agent must refuse instead of guessing.
"""
import sys

from agent.config import load_settings
from agent.controller import AutomationController, AutomationTest
from agent.healing_engine import HealingRecord
from browser.actions import Action
from browser.manager import BrowserManager
from demo_site.app import start_server

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

STEPS = [
    Action("fill", "#username", "alice", name="Enter username"),
    Action("fill", "#password", "secret", name="Enter password"),
    Action("click", "button#login", name="Click login"),
    Action("get_text", "#welcome", name="Read welcome message"),
]


def show_record(rec: HealingRecord) -> None:
    print(f"  [FAIL] {rec.action_name}: '{rec.original_locator}' -> {rec.failure_type}")
    print(f"         inspected {rec.dom_elements} elements; top candidates:")
    for c in rec.candidate_locators[:3]:
        print(f"           {c['score']:.2f}  {c['strategy']:<10} {c['locator']}")
    llm = rec.llm.get("status", "")
    print(f"         selected: {rec.selected_locator or '-'} ({rec.strategy or '-'})  confidence {rec.confidence:.2f}"
          f"  source={rec.source}  llm={llm}")
    if rec.validation_attempts:
        print(f"         validation: {[a['valid'] for a in rec.validation_attempts]}   "
              f"retry: {rec.retry_result.get('success')} ({rec.retry_result.get('attempts')} attempt(s))   "
              f"healing took {rec.execution_time_ms} ms")
    print(f"         -> {rec.message}")


def run(controller: AutomationController, name: str, version: str, base: str, steps=STEPS) -> None:
    print(f"\n=== {name} ===")
    result = controller.run_test(AutomationTest(f"login flow @{version}", f"{base}/{version}", steps, suite="login flow"))
    for s in result.steps:
        print(f"  [{s.status.upper():<13}] {s.name:<24} {s.locator_used}")
    print(f"  RESULT: {result.status.upper()} in {result.duration_ms} ms"
          + (f" - {result.message}" if result.message else ""))


if __name__ == "__main__":
    settings = load_settings()
    server = start_server(port=0)
    base = f"http://127.0.0.1:{server.server_address[1]}"
    with BrowserManager(headless=settings.headless, slow_mo=300) as bm:
        controller = AutomationController(bm, settings, on_record=show_record)
        print(f"LLM: {controller.engine.provider.name if controller.engine.provider else 'none (deterministic mode)'}"
              f" | confidence threshold: {settings.confidence_threshold}")
        run(controller, "1. Baseline: UI v1 (locators work)", "v1", base)
        for v, label in (("v2", "data-testid"), ("v3", "renamed fields + text"), ("v4", "hashed classes + labels")):
            run(controller, f"2. UI {v} changed ({label}): same test, old locators", v, base)
        run(controller, "3. Missing target: must NOT guess", "v1", base,
            [Action("click", "button#checkout", name="Click checkout")])
        print(f"\nHealed locators this session: {len(controller.healed)}")
        for (test, old), new in controller.healed.items():
            print(f"  {test}: {old}  ->  {new}")
    server.shutdown()
