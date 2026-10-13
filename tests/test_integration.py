"""End-to-end tests in a real Firefox browser (skipped automatically if Firefox is missing)."""
import pytest

from agent.config import Settings
from agent.controller import AutomationController, AutomationTest
from browser.actions import Action

STEPS = [
    Action("fill", "#username", "alice", name="Enter username"),
    Action("fill", "#password", "secret", name="Enter password"),
    Action("click", "button#login", name="Click login"),
]


@pytest.fixture(scope="module")
def live(browser_manager, demo_base_url):
    controller = AutomationController(browser_manager, Settings(action_timeout_ms=3000), screenshot_dir=None)
    return controller, demo_base_url


def case(base, version, steps=STEPS):
    return AutomationTest(f"login@{version}", f"{base}/{version}", steps, suite="login")


def test_baseline_passes_on_v1(live):
    controller, base = live
    result = controller.run_test(case(base, "v1"))
    assert result.status == "passed", [(s.name, s.status, s.error) for s in result.steps]
    assert controller.baselines


@pytest.mark.parametrize("version", ["v2", "v3", "v4"])
def test_old_locators_are_healed_on_changed_ui(live, version):
    controller, base = live
    result = controller.run_test(case(base, version))
    assert result.status == "healed", [(s.name, s.status, s.error) for s in result.steps]
    assert all(h.validation_result["valid"] and h.retry_result["success"] for h in result.healing_records)


def test_missing_target_is_refused_and_nothing_is_clicked(live):
    controller, base = live
    result = controller.run_test(case(base, "v1", [Action("click", "button#checkout", name="Click checkout")]))
    assert result.status == "manual_review"
    assert result.healing_records[0].retry_result == {}


def test_v4_welcome_step_heals_without_embedding_the_typed_username(live):
    controller, base = live
    controller.run_test(case(base, "v1", [Action("fill", "#username", "alice", name="Enter username"),
                                         Action("fill", "#password", "secret", name="Enter password"),
                                         Action("click", "button#login", name="Click login"),
                                         Action("get_text", "#welcome", name="Read welcome message")]))
    steps = [Action("fill", "#username", "alice", name="Enter username"), Action("fill", "#password", "secret", name="Enter password"),
             Action("click", "button#login", name="Click login"), Action("get_text", "#welcome", name="Read welcome message")]
    result = controller.run_test(case(base, "v4", steps))
    welcome = result.healing_records[-1]
    assert welcome.status == "healed" and "alice" not in welcome.selected_locator
