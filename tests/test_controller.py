from agent.config import Settings
from agent.controller import AutomationController, AutomationTest
from browser.actions import Action
from tests.fakes import FakeManager, FakePage

STEPS = [
    Action("fill", "#username", "alice", name="Enter username"),
    Action("fill", "#password", "secret", name="Enter password"),
    Action("click", "button#login", name="Click login"),
]


def setup(versions=("v1", "v2")):
    pages = {f"http://demo/{v}": FakePage.version(v) for v in versions}
    manager = FakeManager(pages)
    controller = AutomationController(manager, Settings(), screenshot_dir=None)
    return manager, controller, pages


def test(version, name=None, steps=None):
    return AutomationTest(name or f"login@{version}", f"http://demo/{version}", steps or STEPS, suite="login")


def test_passing_run_learns_baselines():
    _, c, _ = setup()
    r = c.run_test(test("v1"))
    assert r.status == "passed" and [s.status for s in r.steps] == ["passed"] * 3
    assert len(c.baselines) == 3 and ("login", "button#login") in c.baselines


def test_changed_ui_is_healed_and_run_continues():
    _, c, pages = setup()
    c.run_test(test("v1"))
    r = c.run_test(test("v2"))
    assert r.status == "healed" and [s.status for s in r.steps] == ["healed"] * 3
    assert len(r.healing_records) == 3 and all(h.healed for h in r.healing_records)
    assert ("click", '[data-testid="login-button"]') in pages["http://demo/v2"].log
    assert c.healed[("login@v2", "button#login")] == '[data-testid="login-button"]'


def test_healed_locator_is_reused_without_healing_again():
    _, c, _ = setup()
    c.run_test(test("v2"))
    again = c.run_test(test("v2"))
    assert again.status == "passed" and again.healing_records == []
    assert again.steps[2].locator_used == '[data-testid="login-button"]'


def test_low_confidence_stops_run_and_skips_remaining_steps():
    _, c, _ = setup()
    steps = [Action("click", "button#checkout", name="Click checkout"), *STEPS]
    r = c.run_test(test("v1", steps=steps))
    assert r.status == "manual_review" and "manual review" in r.message
    assert [s.status for s in r.steps] == ["manual_review", "skipped", "skipped", "skipped"]


def test_non_locator_failure_fails_the_test():
    pages = {"http://demo/v1": FakePage.version("v1")}
    pages["http://demo/v1"].invalid.add("button[")
    c = AutomationController(FakeManager(pages), Settings(), screenshot_dir=None)
    r = c.run_test(AutomationTest("bad", "http://demo/v1", [Action("click", "button[")]))
    assert r.status == "failed" and r.healing_records[0].status == "not_healable"


def test_browser_crash_restarts_and_reruns_the_test():
    manager, c, pages = setup(("v1",))
    pages["http://demo/v1"].crashed = True
    r = c.run_test(test("v1"))
    assert r.status == "passed" and r.crash_restarts == 1 and manager.restarts == 1
    assert manager.goto_calls.count("http://demo/v1") == 2


def test_unrecoverable_crash_is_reported():
    manager, c, pages = setup(("v1",))
    page = pages["http://demo/v1"]
    page.crashed = True
    manager.ensure_alive = lambda: setattr(page, "crashed", True)
    r = c.run_test(test("v1"))
    assert r.status == "failed" and "crashed" in r.message


def test_on_record_callback_receives_each_record():
    seen = []
    _, c, _ = setup()
    c.on_record = seen.append
    c.run_test(test("v2"))
    assert len(seen) == 3 and all(rec.test_name == "login@v2" for rec in seen)


def test_learning_can_be_disabled():
    _, c, _ = setup()
    c.learn_baselines = False
    c.run_test(test("v1"))
    assert c.baselines == {}


def test_on_event_stream_and_to_dict():
    events = []
    _, c, _ = setup()
    c.on_event = lambda kind, payload: events.append((kind, payload))
    result = c.run_test(test("v2", steps=STEPS[:1]))
    assert [k for k, _ in events] == ["step_start", "failure", "healing", "step_done"]
    assert events[1][1]["failure"]["failure_type"] == "locator_not_found"
    assert events[2][1]["record"]["status"] == "healed"
    d = result.to_dict()
    assert d["status"] == "healed" and d["healing_count"] == 1 and d["steps"][0]["healing_status"] == "healed"


def test_listener_errors_never_break_a_test():
    _, c, _ = setup()

    def boom(kind, payload):
        raise RuntimeError("UI exploded")
    c.on_event = boom
    assert c.run_test(test("v1")).status == "passed"


def test_crash_detected_after_navigation_restarts_the_test():
    manager, c, pages = setup(("v1",))
    page = pages["http://demo/v1"]
    original_goto = manager.goto
    state = {"first": True}

    def goto_then_crash(url):
        original_goto(url)
        if state["first"]:
            state["first"] = False
            page.crashed = True                      # the browser dies right after the page loads
    manager.goto = goto_then_crash
    r = c.run_test(test("v1"))
    assert r.status == "passed" and r.crash_restarts == 1 and manager.restarts == 1


def test_baseline_learning_problems_never_fail_a_passing_test():
    _, c, pages = setup(("v1",))
    pages["http://demo/v1"].fail_content = True       # inspect_dom (used while learning) will raise
    r = c.run_test(test("v1"))
    assert r.status == "passed" and c.baselines == {}


def test_controller_passes_typed_values_to_the_engine_as_volatile():
    seen = {}
    _, c, _ = setup()
    real = c.engine.heal

    def spy(page, action, failure, **kw):
        seen["volatile"] = kw.get("volatile_values")
        return real(page, action, failure, **kw)
    c.engine.heal = spy
    c.run_test(test("v2", steps=STEPS[:1]))
    assert seen["volatile"] == ["alice"]
