"""Error-handling paths: odd failures must degrade to a clear result, never an unhandled exception."""
import pytest

from agent.candidate_scorer import (
    NO_EVIDENCE_CAP, AMBIGUOUS_CAP, ScoredCandidate, ScoringResult, build_profile, evidence_score,
    parse_locator_hints, score_candidates,
)
from agent.config import Settings
from agent.dom_inspector import ElementInfo, inspect_dom, parse_html
from agent.failure_detector import FailureType, classify_failure, detect_failure
from agent.healing_engine import HealingEngine
from agent.llm_reasoner import (
    LLMDecision, LLMError, LLMResult, combine, extract_json,
)
from agent.locator_generator import CandidateLocator
from agent.locator_validator import validate_locator
from browser.actions import Action, ActionResult, execute_action
from tests.fakes import CRASH, FakePage


class Stub:
    """Minimal page whose every capability can be made to fail."""
    def __init__(self, count=1, visible=True, enabled=True, count_error=None, visible_error=None,
                 evaluate_error=None, url_error=False, title_error=False, shot_error=False):
        self.n, self.vis, self.en = count, visible, enabled
        self.count_error, self.visible_error, self.evaluate_error = count_error, visible_error, evaluate_error
        self.url_error, self.title_error, self.shot_error = url_error, title_error, shot_error

    @property
    def url(self):
        if self.url_error:
            raise RuntimeError("no url")
        return "http://x"

    def title(self):
        if self.title_error:
            raise RuntimeError("no title")
        return "T"

    def screenshot(self, path):
        if self.shot_error:
            raise OSError("disk full")

    def locator(self, sel):
        return StubLocator(self)


class StubLocator:
    def __init__(self, page): self.p = page

    def count(self):
        if self.p.count_error:
            raise self.p.count_error
        return self.p.n

    def is_visible(self):
        if self.p.visible_error:
            raise self.p.visible_error
        return self.p.vis

    def is_enabled(self): return self.p.en

    def evaluate(self, js, timeout=None):
        if self.p.evaluate_error:
            raise self.p.evaluate_error
        return {"tag": "button", "id": "a", "name": "", "testid": "", "aria": "", "text": "A"}

    def wait_for(self, state="visible", timeout=None):
        if not self.p.vis:
            raise TimeoutError("Timeout 100ms exceeded.\nCall log")


CLICK = Action("click", "button#a", name="Click a")


def fail(error="Timeout 3000ms exceeded."):
    return ActionResult(False, CLICK, error, 3000.0)


# --- failure classification when the page itself misbehaves ---------------------------------
def test_crash_discovered_while_counting_is_a_crash():
    ftype, _ = classify_failure(Stub(count_error=Exception(CRASH)), CLICK, "Timeout 3000ms exceeded.")
    assert ftype == FailureType.BROWSER_CRASH


def test_unexpected_error_while_counting_is_unknown():
    ftype, _ = classify_failure(Stub(count_error=Exception("weird")), CLICK, "Timeout")
    assert ftype == FailureType.UNKNOWN


def test_invalid_selector_discovered_while_counting():
    ftype, _ = classify_failure(Stub(count_error=Exception('Unexpected token "" while parsing')), CLICK, "Timeout")
    assert ftype == FailureType.INVALID_SELECTOR


def test_cannot_read_visibility_is_unknown_not_a_crash():
    ftype, count = classify_failure(Stub(visible_error=RuntimeError("detached")), CLICK, "Timeout")
    assert (ftype, count) == (FailureType.UNKNOWN, 1)


def test_report_survives_missing_url_title_and_screenshot(tmp_path):
    report = detect_failure(Stub(count=0, url_error=True, title_error=True, shot_error=True), fail(), tmp_path)
    assert report.url == "" and report.title == "" and report.screenshot_path is None
    assert report.failure_type == FailureType.NOT_FOUND


def test_report_summary_text():
    healable = detect_failure(Stub(count=0), fail())
    assert healable.summary() == "locator_not_found on 'button#a' (healable)"
    not_healable = detect_failure(Stub(count_error=Exception('Unexpected token "" while parsing')), fail())
    assert "not healable" in not_healable.summary()


# --- executor: assert_visible and odd inputs ---------------------------------------------------
def test_assert_visible_success_and_failure():
    ok = execute_action(Stub(visible=True), Action("assert_visible", "#a"), timeout_ms=100)
    bad = execute_action(Stub(visible=False), Action("assert_visible", "#a"), timeout_ms=100)
    assert ok.success and not bad.success and "Timeout" in bad.error


def test_empty_error_message_still_gives_readable_error():
    class Silent(Stub):
        def locator(self, sel):
            raise ValueError()
    r = execute_action(Silent(), Action("click", "x"))
    assert not r.success and r.error == "ValueError"


# --- validator -----------------------------------------------------------------------------------
def test_validator_reports_inspection_failure():
    v = validate_locator(Stub(evaluate_error=RuntimeError("element detached")), "button#a", None, "click")
    assert not v.valid and "could not inspect the matched element: element detached" in v.reason


# --- DOM inspector edge cases ---------------------------------------------------------------------
def test_describe_marks_hidden_and_disabled():
    el = parse_html('<body><button id=a disabled hidden>x</button></body>')[0]
    text = el.describe()
    assert "HIDDEN" in text and "DISABLED" in text


def test_roles_explicit_and_implicit():
    els = {e.id: e for e in parse_html(
        '<body><div id=a role="tab menu">x</div><input id=b><textarea id=c></textarea>'
        '<select id=d></select><a id=e>no href</a></body>')}
    assert els["a"].role == "tab" and els["b"].role == "textbox" and els["c"].role == "textbox"
    assert els["d"].role == "combobox" and els["e"].role == ""


def test_inspect_dom_tolerates_a_page_without_url_or_title():
    class Page(Stub):
        def content(self): return "<body><button>x</button></body>"
        def evaluate(self, js): raise RuntimeError
    snap = inspect_dom(Page(url_error=True, title_error=True))
    assert snap.url == "" and snap.title == "" and len(snap.elements) == 1


# --- scorer edge cases ------------------------------------------------------------------------------
def test_hints_for_css_prefixed_locator():
    assert parse_locator_hints("css=button#login")["id"] == "login"


def test_profile_collects_name_attribute_for_fuzzy_matching():
    profile = build_profile('input[name="email"]', "fill", "Enter email")
    assert "email" in profile.variants[0].attr_values


def test_evidence_is_zero_without_any_intent():
    assert evidence_score({"text_match": None, "attribute_similarity": None, "semantic_similarity": None}) == 0.0


def test_scorer_skips_candidates_without_an_element():
    orphan = CandidateLocator("x", "id", 0.8, 1, [1], 1, "", None)
    result = score_candidates([orphan], build_profile("button#a"))
    assert result.best is None and result.confidence == 0.0


# --- combining LLM and scorer: every safety cap ----------------------------------------------------------
def element(i=1):
    return ElementInfo(index=i, tag="button", text="Go")


def scored(i, unique=True, evidence=0.8, score=0.7):
    cand = CandidateLocator(f"loc{i}", "id", 0.8, i, [i] if unique else [i, 99], 1 if unique else 2, "", element(i))
    return ScoredCandidate(cand, score, evidence, 0.8, {})


def llm_choosing(sc, confidence=1.0):
    return LLMResult("ok", LLMDecision(True, sc, sc.candidate.locator, "id", confidence, "yes", True))


def scoring_for(best, confidence=0.7):
    return ScoringResult([best], confidence, None)


def test_agreeing_llm_cannot_lift_an_ambiguous_locator_past_its_cap():
    best = scored(1, unique=False)
    p = combine(scoring_for(best), llm_choosing(best))
    assert p.source == "deterministic+llm" and p.confidence <= AMBIGUOUS_CAP


def test_agreeing_llm_cannot_rescue_missing_evidence():
    best = scored(1, evidence=0.1)
    p = combine(scoring_for(best), llm_choosing(best))
    assert p.confidence <= NO_EVIDENCE_CAP


def test_disagreeing_llm_pick_is_capped_when_ambiguous_or_without_evidence():
    best, other = scored(1), scored(2, unique=False)
    assert combine(scoring_for(best), llm_choosing(other)).confidence <= AMBIGUOUS_CAP
    weak = scored(3, evidence=0.1)
    assert combine(scoring_for(best), llm_choosing(weak)).confidence <= NO_EVIDENCE_CAP


# --- JSON parsing & providers -------------------------------------------------------------------------------
def test_extract_json_skips_broken_braces_before_a_valid_object():
    assert extract_json('see {not json} then {"healed": false}') == {"healed": False}


def test_extract_json_handles_text_with_only_broken_braces():
    assert extract_json("{oops") is None and extract_json("}{") is None


# --- engine construction ---------------------------------------------------------------------------------------
def test_misconfigured_llm_provider_means_deterministic_mode_not_a_crash():
    engine = HealingEngine(Settings(llm_provider="gemini"))
    assert engine.provider is None


def test_engine_accepts_an_explicit_provider_or_none():
    assert HealingEngine(Settings(), provider=None).provider is None
    sentinel = object()
    assert HealingEngine(Settings(), provider=sentinel).provider is sentinel
