"""Locator executor tests. Fake-page tests need no browser; browser tests skip if Chromium is missing."""
import pytest

from browser.actions import Action, execute_action, run_steps


class FakeLocator:
    def __init__(self, page, selector):
        self.page, self.selector = page, selector

    def _check(self):
        if self.selector not in self.page.present:
            raise TimeoutError(f"Timeout waiting for locator('{self.selector}')\nCall log: ...")
        self.page.calls.append(self.selector)

    def click(self, timeout=None): self._check()
    def fill(self, value, timeout=None): self._check(); self.page.filled[self.selector] = value
    def inner_text(self, timeout=None): self._check(); return "Welcome back, alice!"
    def wait_for(self, state="visible", timeout=None): self._check()


class FakePage:
    def __init__(self, present):
        self.present, self.calls, self.filled = set(present), [], {}

    def locator(self, selector):
        return FakeLocator(self, selector)


def test_click_success():
    page = FakePage({"button#login"})
    r = execute_action(page, Action("click", "button#login"))
    assert r.success and r.error is None
    assert page.calls == ["button#login"]


def test_fill_passes_value():
    page = FakePage({"#username"})
    r = execute_action(page, Action("fill", "#username", "alice"))
    assert r.success and page.filled["#username"] == "alice"


def test_get_text_returns_output():
    r = execute_action(FakePage({"#welcome"}), Action("get_text", "#welcome"))
    assert r.success and r.output == "Welcome back, alice!"


def test_broken_locator_returns_failed_result_not_exception():
    r = execute_action(FakePage(set()), Action("click", "button#login"))
    assert not r.success
    assert "Timeout" in r.error
    assert "\n" not in r.error  # only first line kept


def test_unsupported_action_fails_cleanly():
    r = execute_action(FakePage({"x"}), Action("hover", "x"))
    assert not r.success and "Unsupported" in r.error


def test_run_steps_stops_at_first_failure():
    page = FakePage({"#password"})
    steps = [Action("fill", "#username", "a"), Action("fill", "#password", "b")]
    results = run_steps(page, steps)
    assert len(results) == 1 and not results[0].success


def test_run_steps_runs_all_when_ok():
    page = FakePage({"#username", "#password"})
    steps = [Action("fill", "#username", "a"), Action("fill", "#password", "b")]
    assert all(r.success for r in run_steps(page, steps))


# --- real browser tests ------------------------------------------------------
# The `browser_page` fixture lives in tests/conftest.py


def test_v1_login_flow_in_real_browser(browser_page):
    page, base = browser_page
    page.goto(base + "/v1")
    steps = [
        Action("fill", "#username", "alice"),
        Action("fill", "#password", "secret"),
        Action("click", "button#login"),
        Action("get_text", "#welcome"),
    ]
    results = run_steps(page, steps, timeout_ms=2000)
    assert all(r.success for r in results)
    assert results[-1].output == "Welcome back, alice!"


def test_v1_locator_fails_on_v2_in_real_browser(browser_page):
    page, base = browser_page
    page.goto(base + "/v2")
    r = execute_action(page, Action("click", "button#login"), timeout_ms=1000)
    assert not r.success
    assert r.error


def test_new_locator_works_on_v2_in_real_browser(browser_page):
    page, base = browser_page
    page.goto(base + "/v2")
    r = execute_action(page, Action("click", 'button[data-testid="login-button"]'), timeout_ms=2000)
    assert r.success
