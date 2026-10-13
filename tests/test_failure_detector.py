import json

from agent.failure_detector import FailureType, classify_failure, detect_failure
from browser.actions import Action, ActionResult, execute_action


class FakeLocator:
    def __init__(self, page, selector):
        self.page, self.sel = page, selector
        self.spec = page.elements.get(selector, {"count": 0})

    def _boom(self):
        if self.sel in self.page.invalid:
            raise Exception(f'Unexpected token "" while parsing css selector "{self.sel}"')
        if self.page.crashed:
            raise Exception("Target page, context or browser has been closed")

    def count(self): self._boom(); return self.spec["count"]
    def is_visible(self): return self.spec.get("visible", True)
    def is_enabled(self): return self.spec.get("enabled", True)


class FakePage:
    def __init__(self, elements=None, invalid=(), crashed=False, shot_fails=False):
        self.elements, self.invalid, self.crashed, self.shot_fails = elements or {}, set(invalid), crashed, shot_fails
        self.url, self._title = "http://demo/v2", "Demo Login v2"

    def title(self): return self._title
    def locator(self, sel): return FakeLocator(self, sel)

    def screenshot(self, path):
        if self.shot_fails:
            raise RuntimeError("no screenshot")
        open(path, "wb").write(b"png")


def failed(action, error="Timeout 3000ms exceeded."):
    return ActionResult(False, action, error, 3000.0)


CLICK = Action("click", "button#login", name="Click login")


def test_success_returns_none():
    assert detect_failure(FakePage(), ActionResult(True, CLICK)) is None


def test_missing_element_is_not_found_and_healable():
    r = detect_failure(FakePage(), failed(CLICK))
    assert r.failure_type == FailureType.NOT_FOUND
    assert r.healable and r.matched_count == 0


def test_multiple_matches_is_ambiguous_via_count():
    page = FakePage({"button#login": {"count": 3}})
    r = detect_failure(page, failed(CLICK))
    assert r.failure_type == FailureType.AMBIGUOUS and r.matched_count == 3 and r.healable


def test_strict_mode_message_is_ambiguous():
    err = 'Locator.click: Error: strict mode violation: locator("input") resolved to 5 elements:'
    r = detect_failure(FakePage(), failed(Action("click", "input"), err))
    assert r.failure_type == FailureType.AMBIGUOUS and r.matched_count == 5


def test_hidden_element_is_not_healable():
    page = FakePage({"#welcome": {"count": 1, "visible": False}})
    r = detect_failure(page, failed(Action("assert_visible", "#welcome")))
    assert r.failure_type == FailureType.NOT_INTERACTABLE and not r.healable


def test_disabled_button_is_not_interactable_for_click():
    page = FakePage({"button#login": {"count": 1, "visible": True, "enabled": False}})
    assert detect_failure(page, failed(CLICK)).failure_type == FailureType.NOT_INTERACTABLE


def test_invalid_selector_from_error_message():
    err = 'Locator.click: Unexpected token "" while parsing css selector "button["'
    r = detect_failure(FakePage(), failed(Action("click", "button["), err))
    assert r.failure_type == FailureType.INVALID_SELECTOR and not r.healable


def test_invalid_selector_found_when_page_raises():
    page = FakePage(invalid={"button["})
    r = detect_failure(page, failed(Action("click", "button[")))
    assert r.failure_type == FailureType.INVALID_SELECTOR


def test_browser_crash_detected_and_not_healable():
    err = "Page.click: Target page, context or browser has been closed"
    r = detect_failure(FakePage(crashed=True), failed(CLICK, err))
    assert r.failure_type == FailureType.BROWSER_CRASH and not r.healable


def test_unsupported_action():
    res = execute_action(FakePage(), Action("hover", "x"))
    r = detect_failure(FakePage(), res)
    assert r.failure_type == FailureType.UNSUPPORTED_ACTION


def test_unknown_when_element_ok_but_action_failed():
    page = FakePage({"button#login": {"count": 1}})
    ftype, _ = classify_failure(page, CLICK, "Some other error")
    assert ftype == FailureType.UNKNOWN


def test_report_captures_context_and_is_json_serializable():
    r = detect_failure(FakePage(), failed(CLICK))
    assert r.url == "http://demo/v2" and r.title == "Demo Login v2"
    assert r.locator == "button#login" and r.action_type == "click"
    assert r.timestamp.endswith("+00:00")
    assert json.loads(json.dumps(r.to_dict()))["failure_type"] == "locator_not_found"


def test_screenshot_saved(tmp_path):
    r = detect_failure(FakePage(), failed(CLICK), screenshot_dir=tmp_path)
    assert r.screenshot_path and open(r.screenshot_path, "rb").read() == b"png"


def test_screenshot_failure_does_not_break_detection(tmp_path):
    r = detect_failure(FakePage(shot_fails=True), failed(CLICK), screenshot_dir=tmp_path)
    assert r is not None and r.screenshot_path is None


# --- real browser ------------------------------------------------------------
def _run(browser_page, route, action, tmp_path):
    page, base = browser_page
    page.goto(base + route)
    result = execute_action(page, action, timeout_ms=1200)
    return detect_failure(page, result, screenshot_dir=tmp_path)


def test_real_v1_locator_on_v2_is_not_found(browser_page, tmp_path):
    r = _run(browser_page, "/v2", Action("click", "button#login"), tmp_path)
    assert r.failure_type == FailureType.NOT_FOUND and r.healable
    assert r.screenshot_path and __import__("os").path.exists(r.screenshot_path)


def test_real_hidden_message_is_not_interactable(browser_page, tmp_path):
    r = _run(browser_page, "/v1", Action("assert_visible", "#welcome"), tmp_path)
    assert r.failure_type == FailureType.NOT_INTERACTABLE


def test_real_ambiguous_locator(browser_page, tmp_path):
    r = _run(browser_page, "/v1", Action("fill", "input", "x"), tmp_path)
    assert r.failure_type == FailureType.AMBIGUOUS and r.matched_count > 1


def test_real_invalid_selector(browser_page, tmp_path):
    r = _run(browser_page, "/v1", Action("click", "button["), tmp_path)
    assert r.failure_type == FailureType.INVALID_SELECTOR


def test_real_working_locator_has_no_failure(browser_page, tmp_path):
    assert _run(browser_page, "/v1", Action("click", "button#login"), tmp_path) is None
