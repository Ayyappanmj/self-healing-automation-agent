import json
from pathlib import Path

import pytest

from agent.dom_inspector import inspect_dom, parse_html
from agent.locator_generator import (
    accessible_name, generate_candidates, is_compatible,
    looks_generated_class, looks_generated_id,
)

VERSIONS = Path(__file__).resolve().parent.parent / "demo_site" / "versions"


def elements(version: str):
    return parse_html((VERSIONS / f"{version}.html").read_text(encoding="utf-8"))


def locators(cands):
    return {c.locator: c for c in cands}


# --- heuristics ------------------------------------------------------------------
@pytest.mark.parametrize("value", ["css-1a2b3c", "sc-bdVaJa", "c-77b1", "c-a02e", "9f3a1c2d"])
def test_generated_classes_detected(value):
    assert looks_generated_class(value)


@pytest.mark.parametrize("value", ["btn-primary", "status", "card", "col-md-6", "link-muted", "check"])
def test_normal_classes_not_flagged(value):
    assert not looks_generated_class(value)


def test_generated_ids_detected():
    assert looks_generated_id("btn-12345678") and looks_generated_id("a1b2c3d4e5")
    assert not looks_generated_id("login") and not looks_generated_id("f-a1")


# --- v1..v4 candidates ----------------------------------------------------------------
def test_v1_button_candidates():
    c = locators(generate_candidates(elements("v1")))
    assert c["button#login"].strategy == "id" and c["button#login"].unique
    assert 'role=button[name="Login"]' in c
    assert 'button:has-text("Login")' in c
    assert c["main > form > button"].strategy == "dom_path"


def test_v2_button_has_testid_candidate_with_top_priority():
    cands = generate_candidates(elements("v2"))
    assert cands[0].strategy == "test_id"
    c = locators(cands)
    assert c['[data-testid="login-button"]'].base_priority == 0.95
    assert "button#login" not in c            # the old locator is gone


def test_v3_button_uses_aria_and_stable_classes():
    c = locators(generate_candidates(elements("v3")))
    assert c['button[aria-label="Sign in"]'].strategy == "aria_label"
    assert c["button.btn.btn-primary"].strategy == "css_class"
    assert 'role=button[name="Sign in"]' in c


def test_v4_skips_generated_classes_but_keeps_role_text_and_path():
    c = locators(generate_candidates(elements("v4")))
    assert not any(x.strategy == "css_class" and "c-77b1" in x.locator for x in c.values())
    assert 'role=button[name="Log in"]' in c
    assert 'button:has-text("Log in")' in c
    assert c["main > form > div > button"].strategy == "dom_path"


def test_password_field_has_no_role_but_other_strategies():
    c = locators(generate_candidates(elements("v1")))
    assert not any(k.startswith("role=") and "Password" in k for k in c)
    assert "input#password" in c and 'input[placeholder="Password"]' in c
    assert 'xpath=//label[normalize-space(.)="Password"]/following::input[1]' in c


def test_wrapping_label_uses_descendant_xpath():
    c = locators(generate_candidates(elements("v1")))
    assert 'xpath=//label[normalize-space(.)="Remember me"]//input' in c


def test_candidates_sorted_by_priority_desc():
    prios = [c.base_priority for c in generate_candidates(elements("v3"))]
    assert prios == sorted(prios, reverse=True)


# --- duplicates / uniqueness ---------------------------------------------------------------
def test_duplicate_locators_are_merged_and_flagged_not_unique():
    els = parse_html("<body><form><button>Go</button><button>Go</button></form></body>")
    c = locators(generate_candidates(els))
    dup = c['button:has-text("Go")']
    assert dup.match_count == 2 and not dup.unique and len(dup.element_indexes) == 2
    assert c["form > button:nth-of-type(1)"].unique and c["form > button:nth-of-type(2)"].unique


def test_generated_id_gets_low_priority():
    els = parse_html('<body><button id="btn-12345678">x</button></body>')
    cand = next(c for c in generate_candidates(els) if c.strategy == "id")
    assert cand.base_priority == 0.40 and "auto-generated" in cand.description


def test_quotes_are_escaped():
    els = parse_html("<body><button aria-label='Say \"hi\"'>x</button></body>")
    assert 'button[aria-label="Say \\"hi\\""]' in locators(generate_candidates(els))


def test_long_text_and_href_hash_are_skipped():
    els = parse_html('<body><a href="#">' + "word " * 20 + "</a></body>")
    strategies = {c.strategy for c in generate_candidates(els)}
    assert "text" not in strategies and "href" not in strategies


def test_real_href_becomes_candidate():
    els = parse_html('<body><a href="/help">Help</a></body>')
    assert 'a[href="/help"]' in locators(generate_candidates(els))


# --- action filtering ---------------------------------------------------------------------
def ids_for(version, action):
    return {c.element.id for c in generate_candidates(elements(version), action) if c.element.id}


def test_click_filter_excludes_text_paragraphs_and_labels():
    ids = ids_for("v1", "click")
    assert {"login", "forgot"} <= ids and "welcome" not in ids


def test_fill_filter_only_text_inputs():
    assert ids_for("v1", "fill") == {"username", "password"}


def test_get_text_keeps_hidden_elements_but_click_drops_them():
    assert "p#welcome" in locators(generate_candidates(elements("v1"), "get_text"))
    assert "p#welcome" not in locators(generate_candidates(elements("v1"), "click"))


def test_disabled_button_not_clickable():
    els = parse_html("<body><button id=a disabled>A</button><button id=b>B</button></body>")
    assert {c.element.id for c in generate_candidates(els, "click") if c.element.id} == {"b"}


def test_is_compatible_unknown_action_allows_all():
    el = elements("v1")[0]
    assert is_compatible(el, "whatever")


def test_accessible_name_rules():
    by_id = {e.id: e for e in elements("v1") if e.id}
    assert accessible_name(by_id["login"]) == "Login"
    assert accessible_name(by_id["username"]) == "Username"     # from label


# --- output ------------------------------------------------------------------------------------
def test_to_dict_is_json_serializable_and_has_no_element():
    d = generate_candidates(elements("v2"))[0].to_dict()
    assert "element" not in d and d["unique"] is True
    json.dumps(d)


def test_accepts_snapshot_like_object():
    class Snap:
        pass
    s = Snap()
    s.elements = elements("v1")
    assert generate_candidates(s)


# --- dom_path (added to the inspector in this phase) ------------------------------------------
def test_dom_path_uses_nth_of_type_only_when_needed():
    by_id = {e.id: e for e in elements("v1") if e.id}
    assert by_id["login"].dom_path == "main > form > button"
    assert by_id["username"].dom_path == "main > form > input:nth-of-type(1)"
    assert by_id["password"].dom_path == "main > form > input:nth-of-type(2)"


# --- real browser ---------------------------------------------------------------------------------
def test_real_browser_every_candidate_is_valid_and_reachable(browser_page):
    page, base = browser_page
    for route in ("/v1", "/v2", "/v3", "/v4"):
        page.goto(base + route)
        for cand in generate_candidates(inspect_dom(page)):
            count = page.locator(cand.locator).count()      # raises on invalid syntax
            assert count >= 1, (route, cand.locator)
            if cand.strategy in {"test_id", "dom_path"}:
                assert count == 1, (route, cand.locator)


def test_real_browser_new_button_locator_clicks(browser_page):
    from browser.actions import Action, execute_action
    page, base = browser_page
    page.goto(base + "/v2")
    best = next(c for c in generate_candidates(inspect_dom(page), "click") if c.strategy == "test_id"
                and "login" in c.locator)
    assert execute_action(page, Action("click", best.locator), timeout_ms=2000).success


# --- accessible names must match what a real browser computes (found by the real-browser test) -----------------
def test_sibling_label_without_for_is_not_an_accessible_name():
    els = {e.name: e for e in elements("v3") if e.name}
    email = els["email"]
    assert email.label == "Email address" and email.label_source == "sibling"       # still useful as evidence
    assert accessible_name(email) == "you@example.com"                              # but the browser names it by placeholder
    locs = locators(generate_candidates(elements("v3")))
    assert 'role=textbox[name="you@example.com"]' in locs
    assert 'role=textbox[name="Email address"]' not in locs


@pytest.mark.parametrize("html,expected_source,expected_name", [
    ('<body><label for=a>First</label><input id=a placeholder="p"></body>', "for", "First"),
    ('<body><label>Wrapped <input id=a placeholder="p"></label></body>', "wrapping", "Wrapped"),
    ('<body><span id=l>Linked</span><input id=a aria-labelledby=l placeholder="p"></body>', "aria-labelledby", "Linked"),
    ('<body><label>Visual only</label><input id=a placeholder="p"></body>', "sibling", "p"),
])
def test_label_association_decides_the_accessible_name(html, expected_source, expected_name):
    el = next(e for e in parse_html(html) if e.id == "a")
    assert el.label_source == expected_source and accessible_name(el) == expected_name


def test_label_xpath_candidate_still_covers_the_sibling_case():
    assert 'xpath=//label[normalize-space(.)="Email address"]/following::input[1]' in locators(generate_candidates(elements("v3")))



# --- dynamic text must never become a locator (found by the real-browser benchmark run) ---------------------
from agent.locator_generator import looks_dynamic_text  # noqa: E402


@pytest.mark.parametrize("text", ["Welcome back, alice!", "Order 12345 confirmed", "bob@example.com", "Total $19", "Copyright 2026"])
def test_dynamic_looking_text_is_detected(text):
    assert looks_dynamic_text(text, volatile=["alice"]) or looks_dynamic_text(text)


@pytest.mark.parametrize("text", ["Login", "Sign in", "Forgot password?", "Step 2 of 3", "Welcome back"])
def test_stable_text_is_not_flagged(text):
    assert not looks_dynamic_text(text)


def test_values_the_test_typed_are_treated_as_dynamic():
    assert looks_dynamic_text("Welcome back, alice!", volatile=["alice"])
    assert not looks_dynamic_text("Welcome back, alice!")                       # without knowing the typed value
    assert not looks_dynamic_text("Hello", volatile=["al"])                      # very short values are ignored


def test_no_text_or_role_locator_is_generated_from_dynamic_text():
    els = parse_html('<body><p id="m" class="status">Welcome back, alice!</p></body>')
    plain = locators(generate_candidates(els))
    guarded = locators(generate_candidates(els, volatile=["alice"]))
    assert 'p:has-text("Welcome back, alice!")' in plain
    assert not any("alice" in loc for loc in guarded) and "p#m" in guarded and "p.status" in guarded


def test_numbers_in_text_are_not_used_for_locators_even_without_typed_values():
    els = parse_html('<body><button id="b">Pay $19</button><a href="/x">Order 12345</a></body>')
    locs = locators(generate_candidates(els))
    assert not any("$19" in loc or "12345" in loc for loc in locs) and "button#b" in locs


def test_dynamic_text_guard_still_lets_the_engine_heal_with_a_robust_locator():
    from agent.config import Settings
    from agent.healing_engine import HealingEngine
    from browser.actions import Action, execute_action
    from agent.failure_detector import detect_failure
    from tests.fakes import FakePage
    html = '<body><main><p class="c-msg status">Welcome back, alice!</p></main></body>'
    page = FakePage(html)
    act = Action("get_text", "#welcome", name="Read welcome message")
    failure = detect_failure(page, execute_action(page, act, 100))
    rec = HealingEngine(Settings(confidence_threshold=0.2), provider=None).heal(page, act, failure, volatile_values=["alice"])
    assert "alice" not in rec.selected_locator and rec.selected_locator
