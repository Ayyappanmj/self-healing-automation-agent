import json
from pathlib import Path

import pytest

from agent.candidate_scorer import (
    LOW_CONFIDENCE_MESSAGE, _proximity, build_profile, canonical, parse_locator_hints,
    score_candidates, tokenize,
)
from agent.dom_inspector import inspect_dom, parse_html
from agent.locator_generator import CandidateLocator, generate_candidates

VERSIONS = Path(__file__).resolve().parent.parent / "demo_site" / "versions"
THRESHOLD = 0.75


def page_elements(version):
    return parse_html((VERSIONS / f"{version}.html").read_text(encoding="utf-8"))


BASE = {e.id: e for e in page_elements("v1") if e.id}      # baseline fingerprints from the "working" UI


def heal(version, locator, action, name, baseline=None):
    cands = generate_candidates(page_elements(version), action)
    return score_candidates(cands, build_profile(locator, action, name, baseline))


# --- locator hints -----------------------------------------------------------------
def test_hints_css_id_and_tag():
    h = parse_locator_hints("button#login")
    assert h["tag"] == "button" and h["id"] == "login"


def test_hints_attribute_selector():
    h = parse_locator_hints('[data-testid="login-button"]')
    assert h["attrs"] == {"data-testid": "login-button"} and h["tag"] is None


def test_hints_role_name_and_text():
    assert parse_locator_hints('role=button[name="Login"]') == {
        "tag": None, "id": None, "classes": [], "attrs": {}, "texts": ["Login"], "role": "button"}
    assert parse_locator_hints('button:has-text("Log in")')["texts"] == ["Log in"]
    assert parse_locator_hints('text="Login"')["texts"] == ["Login"]


def test_hints_xpath_texts_and_classes():
    assert parse_locator_hints('xpath=//label[normalize-space(.)="Password"]/following::input[1]')["texts"] == ["Password"]
    h = parse_locator_hints("a.btn.primary[href='/x']")
    assert h["tag"] == "a" and h["classes"] == ["btn", "primary"] and h["attrs"]["href"] == "/x"


# --- tokens ---------------------------------------------------------------------------
def test_tokenize_joins_log_in_but_not_verbs():
    assert "login" in tokenize("Log in") and "signin" in tokenize("Sign in")
    assert "clicklogin" not in tokenize("Click login") and tokenize("Click login") == {"login"}


def test_tokenize_camel_case_and_noise():
    assert tokenize("loginButton") == {"login"}
    assert tokenize("c-77b1") == set()


def test_synonyms_share_a_canonical_group():
    assert canonical({"signin"}) == canonical({"login"})
    assert canonical({"passphrase"}) == canonical({"password"})


# --- healing scenarios (no browser needed) -------------------------------------------------
def test_v2_login_button_heals_with_testid():
    r = heal("v2", "button#login", "click", "Click login")
    assert r.best.candidate.strategy == "test_id" and r.best.candidate.locator == '[data-testid="login-button"]'
    assert r.confidence >= 0.9 and r.decision(THRESHOLD) == "heal"


def test_v3_login_button_found_by_synonym_sign_in():
    r = heal("v3", "button#login", "click", "Click login")
    assert r.best.candidate.element.text == "Sign in" and r.decision(THRESHOLD) == "heal"


def test_v4_login_button_found_despite_hashed_classes():
    r = heal("v4", "button#login", "click", "Click login")
    assert r.best.candidate.element.text == "Log in" and r.decision(THRESHOLD) == "heal"


@pytest.mark.parametrize("version,field,value", [("v3", "name", "email"), ("v4", "id", "f-a1")])
def test_username_field_heals(version, field, value):
    r = heal(version, "#username", "fill", "Enter username")
    assert getattr(r.best.candidate.element, field) == value and r.decision(THRESHOLD) == "heal"


@pytest.mark.parametrize("version,field,value", [("v3", "name", "pass"), ("v4", "id", "f-b2")])
def test_password_field_heals(version, field, value):
    r = heal(version, "#password", "fill", "Enter password")
    assert getattr(r.best.candidate.element, field) == value and r.decision(THRESHOLD) == "heal"


def test_forgot_link_picks_right_element_even_when_borderline():
    assert heal("v2", "a#forgot", "click", "Click forgot password").decision(THRESHOLD) == "heal"
    assert heal("v3", "a#forgot", "click", "Click forgot password").best.candidate.element.text == "Reset password"
    assert heal("v4", "a#forgot", "click", "Click forgot password").best.candidate.element.text == "Trouble signing in?"


def test_ambiguous_welcome_message_is_not_auto_healed_without_baseline():
    r = heal("v3", "#welcome", "get_text", "Read welcome message")
    assert r.best.candidate.element.tag == "h1"          # "Welcome back" heading looks plausible
    assert r.decision(THRESHOLD) == "manual_review"


def test_baseline_fingerprint_finds_the_message_element():
    r = heal("v3", "#welcome", "get_text", "Read welcome message", BASE["welcome"])
    assert r.best.candidate.element.id == "flash"


def test_baseline_never_lowers_confidence():
    for version in ("v2", "v3", "v4"):
        without = heal(version, "button#login", "click", "Click login").confidence
        with_base = heal(version, "button#login", "click", "Click login", BASE["login"]).confidence
        assert with_base >= without


# --- refusing to heal ------------------------------------------------------------------------
@pytest.mark.parametrize("version", ["v1", "v3"])
def test_missing_target_gives_low_confidence(version):
    r = heal(version, "button#checkout", "click", "Click checkout")
    assert r.confidence <= 0.4 and r.decision(THRESHOLD) == "manual_review"
    assert "manual review required" in LOW_CONFIDENCE_MESSAGE


def test_twin_buttons_are_not_auto_healed():
    els = parse_html("<body><form><button>Go</button><button>Go</button></form></body>")
    r = score_candidates(generate_candidates(els, "click"), build_profile("button#go", "click", "Click go"))
    assert r.margin is not None and r.margin < 0.05
    assert r.decision(THRESHOLD) == "manual_review"


def test_non_unique_locator_is_capped():
    els = parse_html('<body><button id="a">Go</button></body>')
    good = generate_candidates(els, "click")[0]
    ambiguous = CandidateLocator(good.locator, good.strategy, good.base_priority, good.element_index,
                                 [good.element_index, 99], 2, "", good.element)
    r = score_candidates([ambiguous], build_profile("button#go", "click", "Click go"))
    assert r.confidence <= 0.6


def test_no_candidates():
    r = score_candidates([], build_profile("button#x"))
    assert r.best is None and r.confidence == 0.0 and r.decision(THRESHOLD) == "manual_review"
    assert "No candidate" in r.reason()


def test_threshold_controls_decision():
    r = heal("v3", "button#login", "click", "Click login")
    assert r.should_heal(0.5) and not r.should_heal(0.99)


# --- factors and output ----------------------------------------------------------------------------
def test_visibility_factor_is_neutral_for_get_text():
    r = heal("v1", "#welcome", "get_text", "Read welcome message")
    assert r.best.factors["visibility"] is None


def test_proximity_identical_and_moved():
    same = BASE["login"]
    assert _proximity(same, same) == pytest.approx(1.0)
    v4_button = next(e for e in page_elements("v4") if e.tag == "button")
    assert 0 < _proximity(v4_button, same) < 1


def test_result_is_explainable_and_json_serializable():
    r = heal("v2", "button#login", "click", "Click login")
    d = r.best.to_dict()
    assert {"text_match", "attribute_similarity", "semantic_similarity", "uniqueness"} <= set(d["factors"])
    json.dumps([s.to_dict() for s in r.ranked])
    assert "test_id" in r.reason() and "<button>" in r.reason()


def test_ranking_is_sorted_by_score():
    scores = [s.score for s in heal("v3", "button#login", "click", "Click login").ranked]
    assert scores == sorted(scores, reverse=True)


# --- real browser ------------------------------------------------------------------------------------
@pytest.mark.parametrize("route,expected_text", [("/v2", "Login"), ("/v3", "Sign in"), ("/v4", "Log in")])
def test_real_browser_pipeline_scores_login_button(browser_page, route, expected_text):
    page, base = browser_page
    page.goto(base + route)
    cands = generate_candidates(inspect_dom(page), "click")
    r = score_candidates(cands, build_profile("button#login", "click", "Click login"))
    assert r.best.candidate.element.text == expected_text
    assert r.decision(THRESHOLD) == "heal"
