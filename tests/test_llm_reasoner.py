import json
import os
import re
from pathlib import Path

import pytest
import requests
import httpx
from google.genai import errors

from agent.candidate_scorer import build_profile, score_candidates
from agent.config import Settings
from agent.dom_inspector import parse_html
from agent.llm_reasoner import (
    GeminiProvider, LLMError, LLMReasoner, LLMResult, LLMTimeout, LLMUnavailable, OllamaProvider,
    build_prompt, combine, extract_json, get_provider, parse_decision, reason_about_failure,
    select_prompt_candidates, should_consult_llm,
)
from agent.locator_generator import generate_candidates

VERSIONS = Path(__file__).resolve().parent.parent / "demo_site" / "versions"
THRESHOLD = 0.75


def scored(version, locator, action, name, baseline=None):
    els = parse_html((VERSIONS / f"{version}.html").read_text(encoding="utf-8"))
    profile = build_profile(locator, action, name, baseline)
    return profile, score_candidates(generate_candidates(els, action), profile)


class FakeProvider:
    name, model = "fake", "fake-1"

    def __init__(self, *replies):
        self.replies, self.calls = list(replies), []

    def complete(self, system, prompt, timeout_s):
        self.calls.append(prompt)
        r = self.replies.pop(0) if len(self.replies) > 1 else self.replies[0]
        r = r(prompt) if callable(r) else r
        if isinstance(r, Exception):
            raise r
        return r


def picks(text, confidence=0.9, reason="matches"):
    """A fake 'model' that finds the numbered candidate whose element line contains `text`."""
    def reply(prompt):
        number = None
        for line in prompt.splitlines():
            m = re.match(r"(\d+)\. locator", line)
            if m:
                number = int(m.group(1))
            if line.strip().startswith("element:") and text in line:
                return json.dumps({"healed": True, "candidate": number, "confidence": confidence, "reason": reason})
        return json.dumps({"healed": False, "reason": "none match"})
    return reply


# --- JSON extraction ------------------------------------------------------------------
def test_extract_plain_fenced_and_surrounded():
    assert extract_json('{"a": 1}') == {"a": 1}
    assert extract_json('```json\n{"a": 1}\n```') == {"a": 1}
    assert extract_json('Sure! Here you go: {"a": {"b": 2}} Hope it helps') == {"a": {"b": 2}}


def test_extract_rejects_garbage_and_non_objects():
    assert extract_json("no json here") is None and extract_json("") is None
    assert extract_json("[1, 2, 3]") is None


# --- decision validation -----------------------------------------------------------------
def shown_for(version="v3"):
    profile, result = scored(version, "button#login", "click", "Click login")
    return select_prompt_candidates(result)


def test_valid_reply_by_locator_and_by_number():
    shown = shown_for()
    top = shown[0].candidate.locator
    by_loc = parse_decision({"healed": True, "locator": top, "confidence": 0.9, "reason": "r"}, shown)
    by_num = parse_decision({"healed": True, "candidate": 1, "confidence": "90%", "reason": "r"}, shown)
    assert by_loc.valid and by_num.valid and by_num.confidence == 0.9
    assert by_loc.scored is shown[0]


def test_strategy_comes_from_candidate_not_from_model():
    shown = shown_for()
    d = parse_decision({"healed": True, "candidate": 1, "strategy": "xpath", "confidence": 0.8}, shown)
    assert d.strategy == shown[0].candidate.strategy


def test_invented_locator_is_rejected():
    d = parse_decision({"healed": True, "locator": "#totally-new", "confidence": 0.99}, shown_for())
    assert not d.valid and "not in the candidate list" in d.rejection


def test_number_and_locator_mismatch_rejected():
    shown = shown_for()
    d = parse_decision({"healed": True, "candidate": 1, "locator": shown[1].candidate.locator,
                        "confidence": 0.9}, shown)
    assert not d.valid and "disagree" in d.rejection


def test_missing_or_bad_confidence_rejected():
    shown = shown_for()
    assert not parse_decision({"healed": True, "candidate": 1}, shown).valid
    assert not parse_decision({"healed": True, "candidate": 1, "confidence": "high"}, shown).valid
    assert not parse_decision({"healed": True, "candidate": 1, "confidence": 250}, shown).valid


def test_healed_false_is_a_valid_decline():
    d = parse_decision({"healed": "false", "reason": "nothing fits"}, shown_for())
    assert d.valid and not d.healed


def test_out_of_range_candidate_number_rejected():
    assert not parse_decision({"healed": True, "candidate": 99, "confidence": 0.9}, shown_for()).valid


# --- prompt -----------------------------------------------------------------------------------
def test_shown_candidates_are_distinct_elements_and_limited():
    profile, result = scored("v3", "button#login", "click", "Click login")
    shown = select_prompt_candidates(result, top_n=3)
    assert len(shown) == 3 and len({s.candidate.element_index for s in shown}) == 3


def test_prompt_contains_context_but_no_values():
    els = parse_html('<body><input id="pw" type="password" value="hunter2"><button id="go">Go</button></body>')
    profile = build_profile("button#login", "click", "Click login")
    result = score_candidates(generate_candidates(els, "click"), profile)
    prompt = build_prompt(profile, select_prompt_candidates(result), url="http://x", title="T", failure_type="locator_not_found")
    assert "Broken locator: button#login" in prompt and "1. locator:" in prompt
    assert "locator_not_found" in prompt and "Page: T (http://x)" in prompt
    assert "hunter2" not in prompt


def test_prompt_includes_baseline_when_present():
    base = next(e for e in parse_html((VERSIONS / "v1.html").read_text()) if e.id == "login")
    profile, result = scored("v3", "button#login", "click", "Click login", base)
    assert "previously looked like" in build_prompt(profile, select_prompt_candidates(result))


# --- consulting the model -------------------------------------------------------------------------
def test_should_consult_only_for_borderline():
    _, high = scored("v2", "button#login", "click", "Click login")          # ~0.99
    _, mid = scored("v3", "a#forgot", "click", "Click forgot password")      # ~0.72
    _, low = scored("v1", "button#checkout", "click", "Click checkout")      # ~0.35 -> still has some evidence
    assert not should_consult_llm(high, THRESHOLD) and should_consult_llm(mid, THRESHOLD)
    assert not should_consult_llm(low, 0.2)           # threshold below confidence
    assert not should_consult_llm(low, THRESHOLD, min_confidence=0.9)


def test_disabled_and_skipped():
    profile, result = scored("v3", "a#forgot", "click", "x")
    assert reason_about_failure(None, profile, result).status == "disabled"
    empty = score_candidates([], profile)
    assert reason_about_failure(FakeProvider("{}"), profile, empty).status == "skipped"


def test_ok_path_records_prompt_and_latency():
    profile, result = scored("v3", "a#forgot", "click", "Click forgot password")
    prov = FakeProvider(picks("Reset password"))
    out = reason_about_failure(prov, profile, result, failure_type="locator_not_found")
    assert out.status == "ok" and out.decision.scored.candidate.element.text == "Reset password"
    assert out.attempts == 1 and out.prompt and out.latency_ms >= 0 and out.provider == "fake"
    json.dumps(out.to_dict())


def test_declined():
    profile, result = scored("v3", "a#forgot", "click", "Click forgot password")
    assert reason_about_failure(FakeProvider(picks("nonexistent text")), profile, result).status == "declined"


def test_hallucinated_locator_rejected():
    profile, result = scored("v3", "a#forgot", "click", "Click forgot password")
    bad = json.dumps({"healed": True, "locator": "a.reset-link", "confidence": 0.99})
    out = reason_about_failure(FakeProvider(bad), profile, result)
    assert out.status == "rejected" and "not in the candidate list" in out.error


def test_bad_json_retries_once_then_succeeds():
    profile, result = scored("v3", "a#forgot", "click", "Click forgot password")
    prov = FakeProvider("I think it's the second one", picks("Reset password"))
    out = reason_about_failure(prov, profile, result)
    assert out.status == "ok" and out.attempts == 2
    assert "not valid JSON" in prov.calls[1]


def test_bad_json_twice_gives_bad_json_status():
    profile, result = scored("v3", "a#forgot", "click", "Click forgot password")
    out = reason_about_failure(FakeProvider("nope"), profile, result)
    assert out.status == "bad_json" and out.attempts == 2 and out.decision is None


@pytest.mark.parametrize("error,status", [(LLMTimeout("slow"), "timeout"), (LLMUnavailable("down"), "unavailable"),
                                           (LLMError("boom"), "error")])
def test_provider_errors_map_to_status(error, status):
    profile, result = scored("v3", "a#forgot", "click", "Click forgot password")
    out = reason_about_failure(FakeProvider(error), profile, result)
    assert out.status == status and out.attempts == 1 and out.error


# --- combining ---------------------------------------------------------------------------------------
def test_no_llm_keeps_deterministic_result():
    _, result = scored("v3", "a#forgot", "click", "Click forgot password")
    p = combine(result, None)
    assert p.source == "deterministic" and p.confidence == result.confidence and p.scored is result.best


def test_failed_or_declined_llm_changes_nothing():
    profile, result = scored("v3", "a#forgot", "click", "Click forgot password")
    for reply in (picks("nonexistent"), LLMTimeout("slow"), "garbage"):
        p = combine(result, reason_about_failure(FakeProvider(reply), profile, result))
        assert p.source == "deterministic" and p.confidence == result.confidence


def test_agreeing_llm_can_lift_borderline_case_over_threshold():
    profile, result = scored("v3", "a#forgot", "click", "Click forgot password")
    assert result.confidence < THRESHOLD
    p = combine(result, reason_about_failure(FakeProvider(picks("Reset password", 0.95)), profile, result))
    assert p.source == "deterministic+llm" and p.should_heal(THRESHOLD) and p.confidence <= 0.95


def test_llm_cannot_rescue_weak_evidence():
    profile, result = scored("v4", "a#forgot", "click", "Click forgot password")   # ~0.51
    p = combine(result, reason_about_failure(FakeProvider(picks("Trouble signing in", 1.0)), profile, result))
    assert p.source == "deterministic+llm" and not p.should_heal(THRESHOLD)


def test_agreeing_llm_boost_is_limited():
    profile, result = scored("v4", "a#forgot", "click", "Click forgot password")
    p = combine(result, reason_about_failure(FakeProvider(picks("Trouble signing in", 1.0)), profile, result))
    assert p.confidence <= result.confidence + 0.15 + 1e-9


def test_disagreement_is_capped_below_default_threshold():
    profile, result = scored("v3", "a#forgot", "click", "Click forgot password")
    p = combine(result, reason_about_failure(FakeProvider(picks('text="Sign in"', 1.0)), profile, result))
    assert p.source == "llm_disagreement" and p.confidence <= 0.70 and not p.should_heal(THRESHOLD)
    assert p.scored.candidate.element.text == "Sign in"


def test_no_candidates_proposal_is_empty():
    profile, _ = scored("v3", "a#forgot", "click", "x")
    p = combine(score_candidates([], profile), None)
    assert p.scored is None and p.locator == "" and not p.should_heal(0.0 + 0.01)


# --- providers (no network: injected fake POST) ------------------------------------------------------------
class Resp:
    def __init__(self, status=200, payload=None, text=""):
        self.status_code, self._payload, self.text = status, payload, text

    def json(self):
        if self._payload is None:
            raise ValueError("no json")
        return self._payload


def recorder(*responses):
    calls = []

    def post(url, json=None, headers=None, timeout=None):
        calls.append({"url": url, "json": json, "headers": headers, "timeout": timeout})
        r = responses[min(len(calls) - 1, len(responses) - 1)]
        if isinstance(r, Exception):
            raise r
        return r
    post.calls = calls
    return post


def test_ollama_request_and_response():
    post = recorder(Resp(payload={"message": {"content": '{"healed": false}'}}))
    out = OllamaProvider("http://localhost:11434/", "llama3.2", post).complete("sys", "hi", 12)
    call = post.calls[0]
    assert out == '{"healed": false}'
    assert call["url"] == "http://localhost:11434/api/chat"
    assert call["json"]["format"] == "json" and call["json"]["stream"] is False
    assert call["json"]["options"]["temperature"] == 0
    assert [m["role"] for m in call["json"]["messages"]] == ["system", "user"]
    assert call["timeout"] == (5, 12)


@pytest.mark.parametrize("failure,expected", [
    (requests.ReadTimeout(), LLMTimeout), (requests.ConnectTimeout(), LLMUnavailable),
    (requests.ConnectionError(), LLMUnavailable), (requests.RequestException(), LLMError),
    (Resp(500, {}, "oops"), LLMError), (Resp(200, None), LLMError), (Resp(200, {"weird": 1}), LLMError),
])
def test_ollama_error_mapping(failure, expected):
    with pytest.raises(expected):
        OllamaProvider("http://x", "m", recorder(failure)).complete("s", "p", 5)


def test_connect_timeout_is_unavailable_not_timeout():
    try:
        OllamaProvider("http://x", "m", recorder(requests.ConnectTimeout())).complete("s", "p", 5)
    except LLMTimeout:
        raise AssertionError("connect timeout must map to LLMUnavailable")
    except LLMUnavailable:
        pass


def test_get_provider_selection():
    assert get_provider(Settings()) is None
    assert isinstance(get_provider(Settings(llm_provider="ollama")), OllamaProvider)
    assert isinstance(get_provider(Settings(
        llm_provider="gemini", gemini_api_key="test-key",
    )), GeminiProvider)
    with pytest.raises(LLMUnavailable, match="GEMINI_API_KEY"):
        get_provider(Settings(llm_provider="gemini"))


def test_gemini_request_and_response():
    class Models:
        def generate_content(self, **kwargs):
            self.kwargs = kwargs
            return type("Response", (), {"text": '{"healed": false}'})()

    class Client:
        models = Models()

    client = Client()
    provider = GeminiProvider("test-key", "gemini-test", client=client)

    assert provider.complete("system rules", "user prompt", 8) == '{"healed": false}'
    call = client.models.kwargs
    assert call["model"] == "gemini-test"
    assert call["contents"] == "user prompt"
    assert call["config"].system_instruction == "system rules"
    assert call["config"].temperature == 0
    assert call["config"].response_mime_type == "application/json"


def test_gemini_client_is_initialized_with_api_key(monkeypatch):
    calls = {}

    class Models:
        def generate_content(self, **kwargs):
            calls["generate_content"] = kwargs
            return type("Response", (), {"text": "OK"})()

    class Client:
        def __init__(self, **kwargs):
            calls["client"] = kwargs
            self.models = Models()

    monkeypatch.setattr("agent.llm_reasoner.genai.Client", Client)
    provider = GeminiProvider("test-key", "gemini-test", timeout_s=8)

    assert provider.complete("system", "prompt", 8) == "OK"
    assert calls["client"]["api_key"] == "test-key"
    assert calls["client"]["http_options"].timeout == 8000
    assert calls["generate_content"]["model"] == "gemini-test"
    assert calls["generate_content"]["contents"] == "prompt"


def test_llm_reasoner_gemini_connection_and_analysis(monkeypatch):
    calls = []

    class Models:
        def generate_content(self, **kwargs):
            calls.append(kwargs)
            return type("Response", (), {"text": "response text"})()

    class Client:
        def __init__(self, **kwargs):
            assert kwargs["api_key"] == "test-key"
            self.models = Models()

    monkeypatch.setattr("agent.llm_reasoner.load_settings", lambda: Settings(
        llm_provider="gemini",
        gemini_api_key="configured-key",
        gemini_model="gemini-configured",
        llm_timeout_s=19,
    ))
    monkeypatch.setattr("agent.llm_reasoner.genai.Client", Client)
    reasoner = LLMReasoner(api_key="test-key", model="gemini-test")

    assert reasoner.client is not None
    assert reasoner.test_connection() is True
    assert reasoner.analyze_failure("analyze this") == "response text"
    assert [call["contents"] for call in calls] == ["Hello", "analyze this"]
    assert all(call["model"] == "gemini-test" for call in calls)


@pytest.mark.parametrize("failure,expected", [
    (httpx.TimeoutException("slow"), LLMTimeout),
    (httpx.ConnectError("offline"), LLMUnavailable),
    (httpx.RequestError("failed"), LLMError),
    (errors.APIError(500, {"error": {"message": "server error"}}), LLMError),
])
def test_gemini_error_mapping(failure, expected):
    class Models:
        def generate_content(self, **kwargs):
            raise failure

    class Client:
        models = Models()

    with pytest.raises(expected):
        GeminiProvider("test-key", "gemini-test", client=Client()).complete("s", "p", 5)


# --- optional live test: only runs when you configure a real LLM ----------------------------------------------
def test_live_llm_roundtrip_if_configured():
    from agent.config import load_settings
    settings = load_settings()
    try:
        provider = get_provider(settings)
    except LLMUnavailable:
        provider = None
    if provider is None or os.getenv("RUN_LIVE_LLM_TEST") != "1":
        pytest.skip("set LLM_PROVIDER in .env and RUN_LIVE_LLM_TEST=1 to run")
    profile, result = scored("v3", "a#forgot", "click", "Click forgot password")
    out = reason_about_failure(provider, profile, result, timeout_s=settings.llm_timeout_s)
    assert out.status in {"ok", "declined", "rejected", "bad_json", "timeout", "unavailable", "error"}
    if out.status == "ok":
        assert out.decision.locator in {s.candidate.locator for s in select_prompt_candidates(result)}
