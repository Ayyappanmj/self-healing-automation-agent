import pytest

from agent.dom_inspector import inspect_dom, parse_html
from agent.locator_validator import (
    IDENTITY_JS, find_element, fingerprint, same_element, validate_locator,
)
from tests.fakes import FakePage, html_of


def element(version, **attrs):
    for e in parse_html(html_of(version)):
        if all(getattr(e, k) == v for k, v in attrs.items()):
            return e
    raise AssertionError(attrs)


def test_valid_locator_passes_all_checks():
    page = FakePage.version("v2")
    expected = element("v2", test_id="login-button")
    v = validate_locator(page, '[data-testid="login-button"]', expected, "click")
    assert v.valid and v.count == 1
    assert v.checks == {"resolves": True, "unique": True, "intended_element": True, "visible": True, "enabled": True}
    assert v.to_dict()["valid"] is True


def test_missing_locator_fails():
    v = validate_locator(FakePage.version("v2"), "button#login", None, "click")
    assert not v.valid and v.count == 0 and "no element" in v.reason


def test_ambiguous_locator_fails():
    v = validate_locator(FakePage.version("v1"), "main > form > input:nth-of-type(1)", None, "click")
    assert v.valid                                   # unique by construction
    page = FakePage.version("v1")
    inputs = [e.index for e in page.elements if e.tag == "input"][:2]
    page.map['input[type="text"]'] = inputs
    bad = validate_locator(page, 'input[type="text"]', None, "fill")
    assert not bad.valid and bad.checks["unique"] is False and "ambiguous" in bad.reason


def test_wrong_element_fails_identity_check():
    page = FakePage.version("v2")
    expected = element("v2", test_id="login-button")
    link = element("v2", test_id="forgot-link")
    page.map["role=button[name=\"Login\"]"] = [link.index]       # locator drifted to another element
    v = validate_locator(page, 'role=button[name="Login"]', expected, "click")
    assert not v.valid and v.checks["intended_element"] is False


def test_invalid_syntax_is_reported_not_raised():
    page = FakePage.version("v2")
    page.invalid.add("button[")
    v = validate_locator(page, "button[", None, "click")
    assert not v.valid and v.checks["resolves"] is False and "could not be evaluated" in v.reason


def test_hidden_element_fails_for_click_but_not_get_text():
    page = FakePage.version("v2")
    hidden = element("v2", test_id="welcome-message")
    loc = '[data-testid="welcome-message"]'
    assert not validate_locator(page, loc, hidden, "click").valid
    assert not validate_locator(page, loc, hidden, "assert_visible").valid
    ok = validate_locator(page, loc, hidden, "get_text")
    assert ok.valid and ok.checks["visible"] is None


def test_disabled_button_fails_for_click():
    page = FakePage('<body><button id="a" disabled>A</button></body>')
    v = validate_locator(page, "button#a", None, "click")
    assert not v.valid and v.checks["enabled"] is False


def test_identity_check_skipped_without_expected():
    v = validate_locator(FakePage.version("v2"), '[data-testid="login-button"]', None, "click")
    assert v.valid and v.checks["intended_element"] is None


def test_same_element_rules():
    btn = element("v3", aria_label="Sign in")
    base = {"tag": "button", "id": "", "name": "", "testid": "", "aria": "Sign in", "text": "Sign in"}
    assert same_element(base, btn)
    assert not same_element({**base, "tag": "a"}, btn)
    assert not same_element({**base, "aria": "x", "text": "y"}, btn)


def test_identity_js_mentions_the_fields_we_compare():
    for word in ("tagName", "data-testid", "aria-label", "innerText"):
        assert word in IDENTITY_JS


def test_fingerprint_and_find_element_roundtrip():
    page = FakePage.version("v1")
    fp = fingerprint(page, "button#login")
    assert fp["tag"] == "button" and fp["id"] == "login" and fp["text"] == "Login"
    found = find_element(inspect_dom(page), fp)
    assert found is not None and found.id == "login"


def test_fingerprint_none_for_missing_or_ambiguous():
    page = FakePage.version("v2")
    assert fingerprint(page, "button#login") is None
    page.map["x"] = [e.index for e in page.elements[:2]]
    assert fingerprint(page, "x") is None


def test_find_element_none_when_ambiguous():
    snap = inspect_dom(FakePage("<body><button>Go</button><button>Go</button></body>"))
    assert find_element(snap, {"tag": "button", "id": "", "name": "", "testid": "", "aria": "", "text": "Go"}) is None
