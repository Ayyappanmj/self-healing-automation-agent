import json

import pytest

from agent.config import Settings
from agent.failure_detector import detect_failure
from agent.healing_engine import (
    ERROR, HEALED, MANUAL_REVIEW, NO_CANDIDATES, NOT_HEALABLE, RETRY_FAILED, SUCCESS_MESSAGE,
    VALIDATION_FAILED, HealingEngine,
)
from agent.llm_reasoner import LLMUnavailable
from agent.candidate_scorer import LOW_CONFIDENCE_MESSAGE
from agent.dom_inspector import parse_html
from browser.actions import Action, execute_action
from tests.fakes import FakePage, FakeProvider, html_of

LOGIN = Action("click", "button#login", name="Click login")


def run(page, action, settings=None, provider=None, baseline=None, **kw):
    """Fail the action for real, detect the failure, then heal."""
    settings = settings or Settings()
    result = execute_action(page, action, timeout_ms=100)
    failure = detect_failure(page, result)
    assert failure is not None
    return HealingEngine(settings, provider=provider).heal(page, action, failure, test_name="t", baseline=baseline, **kw)


def test_heals_v2_login_button_end_to_end():
    page = FakePage.version("v2")
    rec = run(page, LOGIN)
    assert rec.status == HEALED and rec.message == SUCCESS_MESSAGE and rec.healed
    assert rec.selected_locator == '[data-testid="login-button"]' and rec.strategy == "test_id"
    assert rec.validation_result["valid"] and rec.retry_result["success"] and rec.retry_result["attempts"] == 1
    assert rec.confidence >= 0.9 and rec.source == "deterministic" and rec.llm == {"status": "disabled"}
    assert ("click", '[data-testid="login-button"]') in page.log            # the retry really acted
    assert rec.original_locator == "button#login" and rec.failure_type == "locator_not_found"
    assert rec.dom_elements > 0 and rec.execution_time_ms >= 0 and rec.candidate_locators


def test_record_has_all_required_log_fields_and_is_json():
    d = run(FakePage.version("v2"), LOGIN).to_dict()
    for key in ("timestamp", "test_name", "action", "original_locator", "failure_reason", "candidate_locators",
                "selected_locator", "confidence", "healing_reason", "validation_result", "retry_result",
                "execution_time_ms"):
        assert key in d
    json.dumps(d)


@pytest.mark.parametrize("version", ["v3", "v4"])
def test_heals_harder_versions(version):
    rec = run(FakePage.version(version), LOGIN)
    assert rec.status == HEALED and rec.confidence >= 0.75


def test_low_confidence_does_not_perform_the_action():
    page = FakePage.version("v1")
    rec = run(page, Action("click", "button#checkout", name="Click checkout"))
    assert rec.status == MANUAL_REVIEW and rec.message == LOW_CONFIDENCE_MESSAGE
    assert page.log == [] and rec.retry_result == {} and rec.validation_attempts == []


def test_threshold_is_respected():
    rec = run(FakePage.version("v2"), LOGIN, settings=Settings(confidence_threshold=1.01))
    assert rec.status == MANUAL_REVIEW and rec.selected_locator          # proposal recorded, not used


def test_invalid_selector_is_not_healable():
    page = FakePage.version("v1")
    page.invalid.add("button[")
    rec = run(page, Action("click", "button["))
    assert rec.status == NOT_HEALABLE and rec.dom_elements == 0 and page.log == []


def test_no_compatible_elements():
    page = FakePage("<body><p>nothing to type into</p></body>")
    rec = run(page, Action("fill", "#username", "x", name="Enter username"))
    assert rec.status == NO_CANDIDATES


def test_validation_failure_falls_back_to_next_locator_for_same_element():
    page = FakePage.version("v2")
    page.map['[data-testid="login-button"]'] = []                         # first choice no longer resolves
    rec = run(page, LOGIN)
    assert rec.status == HEALED and rec.strategy == "role_name"
    assert [a["valid"] for a in rec.validation_attempts] == [False, True]


def test_max_healing_attempts_limits_fallbacks():
    page = FakePage.version("v2")
    page.map['[data-testid="login-button"]'] = []
    rec = run(page, LOGIN, settings=Settings(max_healing_attempts=1))
    assert rec.status == VALIDATION_FAILED and len(rec.validation_attempts) == 1
    assert "manual review" in rec.message and page.log == []


def test_validation_rejects_locator_that_points_elsewhere():
    page = FakePage.version("v2")
    link = next(e for e in page.elements if e.test_id == "forgot-link")
    page.map['[data-testid="login-button"]'] = [link.index]
    page.map['role=button[name="Login"]'] = [link.index]
    page.map['button:has-text("Login")'] = [link.index]
    rec = run(page, LOGIN, settings=Settings(max_healing_attempts=3))
    assert rec.status == VALIDATION_FAILED
    assert all(a["checks"].get("intended_element") is False for a in rec.validation_attempts[:3])


def test_retry_limit_when_action_keeps_failing():
    page = FakePage.version("v2")
    page.fail_actions.add(('[data-testid="login-button"]', "click"))
    rec = run(page, LOGIN, settings=Settings(action_retries=3))
    assert rec.status == RETRY_FAILED and rec.retry_result["attempts"] == 3
    assert "still failed" in rec.message and not rec.healed


def test_engine_survives_browser_crash_during_healing():
    page = FakePage.version("v2")
    result = execute_action(page, LOGIN, timeout_ms=100)
    failure = detect_failure(page, result)
    page.fail_content = True
    rec = HealingEngine(Settings(), provider=None).heal(page, LOGIN, failure)
    assert rec.status == ERROR and "Healing aborted" in rec.message


# --- LLM integration ----------------------------------------------------------------------
def picks(text, confidence=0.95):
    import re

    def reply(prompt):
        number = None
        for line in prompt.splitlines():
            m = re.match(r"(\d+)\. locator", line)
            if m:
                number = int(m.group(1))
            if line.strip().startswith("element:") and text in line:
                return json.dumps({"healed": True, "candidate": number, "confidence": confidence, "reason": "same link"})
        return json.dumps({"healed": False})
    return reply


FORGOT = Action("click", "a#forgot", name="Click forgot password")


def test_llm_not_consulted_when_deterministic_is_confident():
    prov = FakeProvider(picks("x"))
    rec = run(FakePage.version("v2"), LOGIN, provider=prov)
    assert rec.llm == {"status": "not_consulted"} and prov.calls == []


def test_llm_agreement_heals_borderline_case():
    page = FakePage.version("v3")
    assert run(FakePage.version("v3"), FORGOT).status == MANUAL_REVIEW        # without LLM
    rec = run(page, FORGOT, provider=FakeProvider(picks("Reset password")))
    assert rec.status == HEALED and rec.source == "deterministic+llm" and rec.llm["status"] == "ok"
    assert rec.confidence > rec.deterministic_confidence


def test_llm_outage_falls_back_to_deterministic():
    rec = run(FakePage.version("v3"), FORGOT, provider=FakeProvider(LLMUnavailable("down")))
    assert rec.status == MANUAL_REVIEW and rec.llm["status"] == "unavailable"
    assert rec.source == "deterministic"


def test_llm_disagreement_goes_to_manual_review():
    rec = run(FakePage.version("v3"), FORGOT, provider=FakeProvider(picks('text="Sign in"', 1.0)))
    assert rec.status == MANUAL_REVIEW and rec.source == "llm_disagreement"


def test_baseline_is_used_when_given():
    base = next(e for e in parse_html(html_of("v1")) if e.id == "welcome")
    act = Action("get_text", "#welcome", name="Read welcome message")
    with_base = run(FakePage.version("v3"), act, baseline=base)
    assert with_base.selected_locator and with_base.deterministic_confidence > 0
