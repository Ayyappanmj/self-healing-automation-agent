"""AI locator reasoner: optional LLM second opinion on borderline healing cases.

Principles
- The deterministic scorer always runs first; the LLM is consulted only when the result is
  borderline (below the threshold but with some evidence).
- The LLM sees only the top few candidate elements, never the whole page.
- It must choose from the candidate list. Invented locators are rejected.
- It cannot rescue a candidate with no evidence or an ambiguous locator, it can add at most
  0.15 to the scorer's confidence when it agrees, and when it disagrees the result is capped
  below the default threshold.
- Every failure (timeout, server down, bad JSON) falls back to the deterministic result.
"""
from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass
from typing import Any, Callable, Protocol

import httpx
import requests
from google import genai
from google.genai import errors, types

from agent.candidate_scorer import (
    AMBIGUOUS_CAP, LOW_EVIDENCE, NO_EVIDENCE_CAP, ScoredCandidate, ScoringResult, TargetProfile,
)
from agent.config import Settings, load_settings

LLM_MIN_CONFIDENCE = 0.30     # below this there is no real evidence to ask about
TOP_N = 5
MAX_ATTEMPTS = 2              # 1 retry when the reply is not valid JSON
AGREE_CAP = 0.95
AGREE_MAX_BOOST = 0.15         # an agreeing LLM can add at most this much to the scorer's confidence
DISAGREE_CAP = 0.70
CONNECT_TIMEOUT_S = 5


# ---- errors & providers ---------------------------------------------------------
class LLMError(Exception):
    def __init__(self, message: str, status: int | None = None):
        super().__init__(message)
        self.status = status


class LLMTimeout(LLMError):
    pass


class LLMUnavailable(LLMError):
    pass


class LLMProvider(Protocol):
    name: str
    model: str

    def complete(self, system: str, prompt: str, timeout_s: float) -> str: ...


def _post_json(post: Callable, url: str, payload: dict, headers: dict | None, timeout_s: float) -> dict:
    try:
        resp = post(url, json=payload, headers=headers, timeout=(CONNECT_TIMEOUT_S, timeout_s))
    except requests.ConnectTimeout as exc:
        raise LLMUnavailable("Could not connect to the LLM server (connect timeout)") from exc
    except requests.Timeout as exc:
        raise LLMTimeout(f"LLM did not answer within {timeout_s}s") from exc
    except requests.ConnectionError as exc:
        raise LLMUnavailable("Could not connect to the LLM server") from exc
    except requests.RequestException as exc:
        raise LLMError(f"LLM request failed: {type(exc).__name__}") from exc
    if resp.status_code >= 400:
        raise LLMError(f"LLM server returned HTTP {resp.status_code}: {resp.text[:200]}", resp.status_code)
    try:
        return resp.json()
    except ValueError as exc:
        raise LLMError("LLM server reply was not JSON") from exc


class OllamaProvider:
    name = "ollama"

    def __init__(self, base_url: str, model: str, post: Callable | None = None):
        self.url, self.model, self._post = base_url.rstrip("/") + "/api/chat", model, post or requests.post

    def complete(self, system: str, prompt: str, timeout_s: float) -> str:
        payload = {"model": self.model, "stream": False, "format": "json", "options": {"temperature": 0},
                   "messages": [{"role": "system", "content": system}, {"role": "user", "content": prompt}]}
        data = _post_json(self._post, self.url, payload, None, timeout_s)
        try:
            return str(data["message"]["content"])
        except (KeyError, TypeError) as exc:
            raise LLMError("Unexpected Ollama response shape") from exc


class GeminiProvider:
    name = "gemini"

    def __init__(self, api_key: str, model: str, timeout_s: float = 30, client: Any | None = None):
        self.model = model
        self.client = client if client is not None else genai.Client(
            api_key=api_key, http_options=types.HttpOptions(timeout=int(timeout_s * 1000))
        )

    def complete(self, system: str, prompt: str, timeout_s: float) -> str:
        try:
            response = self.client.models.generate_content(
                model=self.model,
                contents=prompt,
                config=types.GenerateContentConfig(
                    system_instruction=system,
                    temperature=0,
                    response_mime_type="application/json",
                ),
            )
        except httpx.TimeoutException as exc:
            raise LLMTimeout(f"LLM did not answer within {timeout_s}s") from exc
        except httpx.ConnectError as exc:
            raise LLMUnavailable("Could not connect to the Gemini API") from exc
        except httpx.RequestError as exc:
            raise LLMError(f"Gemini request failed: {type(exc).__name__}") from exc
        except errors.APIError as exc:
            raise LLMError(f"Gemini API returned HTTP {exc.code}: {exc.message}", exc.code) from exc

        try:
            text = response.text
        except (AttributeError, ValueError) as exc:
            raise LLMError("Unexpected Gemini response shape") from exc
        if not isinstance(text, str) or not text.strip():
            raise LLMError("Gemini returned an empty response")
        return text


class LLMReasoner:
    """Simple connection-check and analysis facade over the configured LLM provider."""

    def __init__(self, provider: str | None = None, api_key: str | None = None,
                 model: str | None = None):
        settings = load_settings()
        self.provider = provider or settings.llm_provider
        self.model = model or (
            settings.gemini_model if self.provider == "gemini" else settings.ollama_model
        )
        self.timeout_s = settings.llm_timeout_s
        self._provider: LLMProvider | None = None

        if self.provider == "gemini":
            key = api_key or settings.gemini_api_key
            if key:
                self._provider = GeminiProvider(key, self.model, self.timeout_s)
        elif self.provider == "ollama":
            self._provider = OllamaProvider(settings.ollama_url, self.model)
        elif self.provider != "none":
            raise LLMError(f"Unsupported provider: {self.provider}")

        self.client = getattr(self._provider, "client", None)

    def test_connection(self) -> bool:
        """Check provider connectivity; deterministic-only mode needs no connection."""
        if self.provider == "none":
            return True
        if self._provider is None:
            raise LLMError("Gemini client not initialized. Check GEMINI_API_KEY.")
        self._provider.complete("", "Hello", self.timeout_s)
        return True

    def analyze_failure(self, prompt: str) -> str:
        """Send a prompt to the configured provider and return its response text."""
        if self.provider == "none":
            raise LLMError("LLM provider is set to none.")
        if self._provider is None:
            raise LLMError("Gemini client not initialized. Check GEMINI_API_KEY.")
        return self._provider.complete("", prompt, self.timeout_s)


def get_provider(settings: Settings, post: Callable | None = None) -> LLMProvider | None:
    """None means deterministic-only mode. Raises LLMUnavailable for a misconfigured provider."""
    if settings.llm_provider == "ollama":
        return OllamaProvider(settings.ollama_url, settings.ollama_model, post)
    if settings.llm_provider == "gemini":
        if not settings.gemini_api_key:
            raise LLMUnavailable("GEMINI_API_KEY is not set")
        return GeminiProvider(settings.gemini_api_key, settings.gemini_model, settings.llm_timeout_s)
    return None


# ---- prompt -------------------------------------------------------------------------
SYSTEM_PROMPT = (
    "You repair broken UI test locators. A test step failed because its locator no longer matches "
    "anything on the page. From the numbered candidate elements, pick the one that is the same UI "
    "element the old locator targeted. Use only the candidates given and never invent a locator. "
    "If none is clearly the same element, answer healed=false. Reply with ONLY one JSON object: "
    '{"healed": true|false, "candidate": <number>, "locator": "<exact locator text of that candidate>", '
    '"strategy": "<its strategy>", "confidence": <0.0-1.0>, "reason": "<one short sentence>"}'
)


def select_prompt_candidates(result: ScoringResult, top_n: int = TOP_N) -> list[ScoredCandidate]:
    """Best-scored locator for each of the top distinct elements."""
    shown: list[ScoredCandidate] = []
    seen: set[int] = set()
    for s in result.ranked:
        if s.candidate.element_index in seen:
            continue
        seen.add(s.candidate.element_index)
        shown.append(s)
        if len(shown) == top_n:
            break
    return shown


def build_prompt(profile: TargetProfile, shown: list[ScoredCandidate], url: str = "", title: str = "",
                 failure_type: str = "") -> str:
    lines = [f"Failed step: {profile.action_name or profile.action_type} (action: {profile.action_type})",
             f"Broken locator: {profile.locator}"]
    if failure_type:
        lines.append(f"Failure: {failure_type}")
    if title or url:
        lines.append(f"Page: {title} ({url})")
    if profile.baseline is not None:
        lines.append("The target previously looked like: " + profile.baseline.describe()[:300])
    lines += ["", "Candidates (best locator per element, with deterministic scores 0-1):"]
    for i, s in enumerate(shown, 1):
        lines.append(f"{i}. locator: {s.candidate.locator} | strategy: {s.candidate.strategy} | score: {s.score:.2f}")
        lines.append(f"   element: {s.candidate.element.describe()[:300]}")
    return "\n".join(lines)


# ---- parsing & validation ----------------------------------------------------------------
def extract_json(text: str) -> dict | None:
    """Pull one JSON object out of a model reply (plain, fenced, or surrounded by prose)."""
    if not text:
        return None
    t = text.strip()
    tries = [t] + [f.strip() for f in re.findall(r"```(?:json)?\s*(.*?)```", t, re.S | re.I)]
    for c in tries:
        try:
            v = json.loads(c)
        except ValueError:
            continue
        if isinstance(v, dict):
            return v
    decoder = json.JSONDecoder()
    for m in re.finditer(r"\{", t):
        try:
            v, _ = decoder.raw_decode(t[m.start():])
        except ValueError:
            continue
        if isinstance(v, dict):
            return v
    return None


def _as_bool(v: Any) -> bool:
    if isinstance(v, str):
        return v.strip().lower() in {"true", "yes", "1"}
    return bool(v)


def _as_confidence(v: Any) -> float | None:
    try:
        x = float(str(v).strip().rstrip("%"))
    except ValueError:
        return None
    if 1 < x <= 100:
        x /= 100
    return x if 0 <= x <= 1 else None


@dataclass
class LLMDecision:
    healed: bool
    scored: ScoredCandidate | None = None
    locator: str = ""
    strategy: str = ""
    confidence: float = 0.0
    reason: str = ""
    valid: bool = False
    rejection: str | None = None


def parse_decision(data: dict, shown: list[ScoredCandidate]) -> LLMDecision:
    """Validate a model reply against the candidates it was shown. Never trusts the locator text."""
    reason = str(data.get("reason", ""))[:300]
    if not _as_bool(data.get("healed")):
        return LLMDecision(False, reason=reason or "Model found no matching candidate", valid=True)
    by_locator = {s.candidate.locator: s for s in shown}
    by_number = None
    try:
        n = int(data.get("candidate"))
        by_number = shown[n - 1] if 1 <= n <= len(shown) else None
    except (TypeError, ValueError):
        pass
    loc = str(data.get("locator", "")).strip()
    chosen = by_locator.get(loc)
    if chosen is not None and by_number is not None and chosen is not by_number:
        return LLMDecision(True, reason=reason, rejection="candidate number and locator disagree")
    chosen = chosen or by_number
    if chosen is None:
        return LLMDecision(True, locator=loc, reason=reason, rejection="locator is not in the candidate list")
    conf = _as_confidence(data.get("confidence"))
    if conf is None:
        return LLMDecision(True, chosen, chosen.candidate.locator, reason=reason, rejection="missing or invalid confidence")
    return LLMDecision(True, chosen, chosen.candidate.locator, chosen.candidate.strategy, conf, reason, True)


# ---- consulting the model ----------------------------------------------------------------------
@dataclass
class LLMResult:
    status: str          # ok | declined | rejected | bad_json | timeout | unavailable | error | disabled | skipped
    decision: LLMDecision | None = None
    provider: str = ""
    model: str = ""
    latency_ms: float = 0.0
    attempts: int = 0
    prompt: str = ""
    raw_text: str = ""
    error: str = ""

    def to_dict(self, include_text: bool = False) -> dict:
        d = self.decision
        out = {"status": self.status, "provider": self.provider, "model": self.model,
               "latency_ms": self.latency_ms, "attempts": self.attempts, "error": self.error,
               "locator": d.locator if d else "", "confidence": d.confidence if d else None,
               "reason": d.reason if d else "", "rejection": d.rejection if d else None}
        if include_text:                       # for the UI: show exactly what was sent and received
            out["prompt"], out["raw_text"] = self.prompt[:6000], self.raw_text[:2000]
        return out


def should_consult_llm(result: ScoringResult, threshold: float, min_confidence: float = LLM_MIN_CONFIDENCE) -> bool:
    """Ask only when the deterministic answer is borderline: some evidence, but under the threshold."""
    return result.best is not None and min_confidence <= result.confidence < threshold


def reason_about_failure(provider: LLMProvider | None, profile: TargetProfile, result: ScoringResult, *,
                         url: str = "", title: str = "", failure_type: str = "", top_n: int = TOP_N,
                         timeout_s: float = 30, max_attempts: int = MAX_ATTEMPTS) -> LLMResult:
    if provider is None:
        return LLMResult("disabled")
    shown = select_prompt_candidates(result, top_n)
    if not shown:
        return LLMResult("skipped", provider=provider.name, model=provider.model)
    prompt = build_prompt(profile, shown, url, title, failure_type)
    out = LLMResult("error", provider=provider.name, model=provider.model, prompt=prompt)
    start = time.perf_counter()
    ask = prompt
    for attempt in range(1, max_attempts + 1):
        out.attempts = attempt
        try:
            out.raw_text = provider.complete(SYSTEM_PROMPT, ask, timeout_s)
        except LLMTimeout as exc:
            out.status, out.error = "timeout", str(exc)
            break
        except LLMUnavailable as exc:
            out.status, out.error = "unavailable", str(exc)
            break
        except LLMError as exc:
            out.status, out.error = "error", str(exc)
            break
        data = extract_json(out.raw_text)
        if data is None:
            out.status, out.error = "bad_json", "Model reply was not valid JSON"
            ask = prompt + "\n\nYour previous reply was not valid JSON. Reply with ONLY the JSON object."
            continue
        out.decision = parse_decision(data, shown)
        out.error = out.decision.rejection or ""
        out.status = "rejected" if not out.decision.valid else ("ok" if out.decision.healed else "declined")
        break
    out.latency_ms = round((time.perf_counter() - start) * 1000, 1)
    out.raw_text = out.raw_text[:2000]
    return out


# ---- combining the two opinions ---------------------------------------------------------------------
@dataclass
class HealingProposal:
    scored: ScoredCandidate | None
    confidence: float
    source: str          # deterministic | deterministic+llm | llm_disagreement
    reason: str
    llm: LLMResult | None = None

    @property
    def locator(self) -> str:
        return self.scored.candidate.locator if self.scored else ""

    def should_heal(self, threshold: float) -> bool:
        return self.scored is not None and self.confidence >= threshold


def combine(result: ScoringResult, llm: LLMResult | None) -> HealingProposal:
    best = result.best
    if best is None:
        return HealingProposal(None, 0.0, "deterministic", result.reason(), llm)
    if llm is None or llm.status != "ok":
        return HealingProposal(best, result.confidence, "deterministic", result.reason(), llm)

    d = llm.decision
    chosen = d.scored
    if set(chosen.candidate.element_indexes) & set(best.candidate.element_indexes):
        blend = 0.5 * result.confidence + 0.5 * d.confidence
        conf = min(AGREE_CAP, result.confidence + AGREE_MAX_BOOST, max(result.confidence, blend))
        if not best.candidate.unique:
            conf = min(conf, AMBIGUOUS_CAP)
        if best.evidence < LOW_EVIDENCE:
            conf = min(conf, NO_EVIDENCE_CAP)
        return HealingProposal(best, round(conf, 4), "deterministic+llm",
                               f"{result.reason()}; LLM agrees: {d.reason}", llm)
    conf = min(DISAGREE_CAP, 0.85 * (0.5 * chosen.score + 0.5 * d.confidence))
    if not chosen.candidate.unique:
        conf = min(conf, AMBIGUOUS_CAP)
    if chosen.evidence < LOW_EVIDENCE:
        conf = min(conf, NO_EVIDENCE_CAP)
    return HealingProposal(chosen, round(conf, 4), "llm_disagreement",
                           f"LLM chose a different element than the scorer: {d.reason}", llm)
