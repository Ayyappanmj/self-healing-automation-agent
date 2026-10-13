"""Candidate scorer: ranks generated locators by how likely they point at the intended element.

Score = 0.65 * evidence + 0.35 * quality
  evidence: does the element look like what the broken locator meant?
            (text match, attribute similarity, semantic similarity, DOM proximity to a baseline)
  quality : is this a good locator? (unique, stable strategy, right element type,
            accessibility info, visible, enabled)

Confidence adjusts the best score for ambiguity: a close rival element lowers it, an
ambiguous locator or missing intent evidence caps it. Everything is deterministic; the
LLM (Phase 8) only sees the top candidates.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from difflib import SequenceMatcher

from agent.dom_inspector import ElementInfo
from agent.locator_generator import CandidateLocator, accessible_name, is_compatible, looks_generated_class

EVIDENCE_WEIGHT = 0.65
QUALITY_WEIGHT = 0.35
INTENT_WEIGHTS = {"text_match": 0.35, "attribute_similarity": 0.30, "semantic_similarity": 0.35}
PROXIMITY_BONUS = 0.30       # baseline proximity can only raise evidence, never lower it
ACTION_WORD_WEIGHT = 0.5     # words from the step name count half as much as locator/baseline tokens
QUALITY_WEIGHTS = {"uniqueness": 0.35, "stability": 0.25, "element_type": 0.15,
                   "accessibility": 0.10, "visibility": 0.10, "enabled": 0.05}
MARGIN_FULL = 0.15            # score lead over the best rival element for full confidence
AMBIGUOUS_CAP = 0.60          # locator matches several elements
NO_EVIDENCE_CAP = 0.40        # nothing links the element to the original intent
LOW_EVIDENCE = 0.25
LOW_CONFIDENCE_MESSAGE = "Healing confidence too low — manual review required."

STOP_WORDS = {
    "the", "a", "an", "in", "on", "of", "to", "into", "for", "and", "or", "me", "my", "your",
    "click", "enter", "type", "fill", "press", "tap", "select", "choose", "read", "get", "open",
    "assert", "verify", "check", "button", "btn", "field", "input", "link", "box", "text", "label",
}
SYNONYM_GROUPS = {
    "g:login": {"login", "signin", "logon", "authenticate"},
    "g:username": {"username", "user", "userid", "email", "account", "handle"},
    "g:password": {"password", "passphrase", "pass", "pwd", "passcode"},
    "g:forgot": {"forgot", "reset", "trouble", "recover", "recovery"},
    "g:remember": {"remember", "keep", "stay"},
    "g:message": {"welcome", "message", "msg", "flash", "status", "alert", "notice", "notification", "result"},
    "g:submit": {"submit", "send", "continue", "confirm"},
}
_CANON = {tok: g for g, toks in SYNONYM_GROUPS.items() for tok in toks}
FAMILIES = [{"button", "a", "summary"}, {"input", "textarea", "select"},
            {"p", "span", "div", "h1", "h2", "h3", "h4", "h5", "h6", "label", "li"}]


# ---- tokens ---------------------------------------------------------------------
def tokenize(text: str) -> set[str]:
    """Lowercased word tokens plus joined neighbours ('log in' -> 'login')."""
    text = re.sub(r"([a-z])([A-Z])", r"\1 \2", text or "")
    words = [w for w in re.split(r"[^A-Za-z0-9]+", text.lower()) if w]
    tokens = {w for w in words if w not in STOP_WORDS and len(w) > 1 and not looks_generated_class(w)}
    # join neighbours ("log in" -> "login") but never through verbs/stop words ("click login")
    tokens |= {a + b for a, b in zip(words, words[1:])
               if len(a + b) > 3 and a not in STOP_WORDS and (b not in STOP_WORDS or b in {"in", "out", "up"})
               and not looks_generated_class(a) and not looks_generated_class(b)}
    return tokens


def canonical(tokens: set[str]) -> set[str]:
    return {_CANON.get(t, t) for t in tokens}


def _weighted(primary: str, secondary: set[str]) -> dict[str, float]:
    """Intent tokens: full weight from the locator/baseline, reduced weight from the step name."""
    weights = {t: 1.0 for t in tokenize(primary)}
    for t in secondary:
        weights.setdefault(t, ACTION_WORD_WEIGHT)
    return weights


def _coverage(intent: dict[str, float], target: set[str]) -> float:
    total = sum(intent.values())
    return sum(w for t, w in intent.items() if t in target) / total if total else 0.0


def _canonical_weights(intent: dict[str, float]) -> dict[str, float]:
    out: dict[str, float] = {}
    for tok, w in intent.items():
        key = _CANON.get(tok, tok)
        out[key] = max(out.get(key, 0.0), w)
    return out


def _ratio(a: str, b: str) -> float:
    a, b = a.strip().lower(), b.strip().lower()
    return SequenceMatcher(None, a, b).ratio() if a and b else 0.0


# ---- locator hints ----------------------------------------------------------------
_ATTR_RE = re.compile(r"\[\s*([\w:-]+)\s*(?:[~|^$*]?=\s*(?:\"([^\"]*)\"|'([^']*)'|([^\]\s]*)))?\s*\]")


def parse_locator_hints(locator: str) -> dict:
    """Extract what a broken locator says about its target (tag, id, classes, attrs, texts, role)."""
    hints: dict = {"tag": None, "id": None, "classes": [], "attrs": {}, "texts": [], "role": None}
    s = locator.strip()
    m = re.match(r"role=(\w+)(?:\[name=(?:\"((?:[^\"\\]|\\.)*)\"|'([^']*)')[^\]]*\])?", s)
    if m:
        hints["role"] = m.group(1)
        name = m.group(2) or m.group(3)
        if name:
            hints["texts"].append(name.replace('\\"', '"'))
        return hints
    if s.startswith("xpath=") or s.startswith("//"):
        hints["texts"] = re.findall(r'"([^"]+)"', s) + re.findall(r"'([^']+)'", s)
        return hints
    if s.startswith("text="):
        hints["texts"].append(s[5:].strip("\"' "))
        return hints
    if s.startswith("css="):
        s = s[4:]
    hints["texts"] += re.findall(r":(?:has-text|text-is|text|text-matches)\(\s*[\"']([^\"']*)[\"']", s)
    for m in _ATTR_RE.finditer(s):
        val = m.group(2) or m.group(3) or m.group(4) or ""
        hints["attrs"][m.group(1).lower()] = val
    bare = _ATTR_RE.sub("", s)
    bare = re.sub(r":[\w-]+(\([^)]*\))?", "", bare)
    bare = bare.split(">")[-1].split()[-1] if bare.strip() else ""
    tag = re.match(r"^([a-zA-Z][\w-]*)", bare)
    hints["tag"] = tag.group(1).lower() if tag else None
    ident = re.search(r"#([\w-]+)", bare)
    hints["id"] = ident.group(1) if ident else None
    hints["classes"] = re.findall(r"\.([\w-]+)", bare)
    return hints


def _stable_classes(el: ElementInfo) -> list[str]:
    return [c for c in el.classes if not looks_generated_class(c)]


def _text_side(el: ElementInfo) -> str:
    return " ".join([el.text, el.aria_label, el.label, el.placeholder, el.title])


def _attr_side(el: ElementInfo) -> str:
    return " ".join([el.id, el.name, el.test_id, " ".join(_stable_classes(el)), el.placeholder,
                     el.aria_label, el.type, " ".join(el.data_attrs.values()), el.href])


@dataclass
class IntentVariant:
    """One view of what the target was: tokens (weighted) plus raw strings for fuzzy matching."""
    text_intent: dict[str, float] = field(default_factory=dict)
    attr_intent: dict[str, float] = field(default_factory=dict)
    text_value: str = ""
    attr_values: list[str] = field(default_factory=list)


@dataclass
class TargetProfile:
    """What the automation was looking for.

    variants[0] comes from the broken locator + step name. If a baseline fingerprint of the
    element from a passing run exists, variants[1] comes from it. Scoring takes the better
    match per factor, so a baseline can only help, never hurt (its attributes may be stale).
    """
    locator: str
    action_type: str = "click"
    action_name: str = ""
    tag: str | None = None
    role: str | None = None
    variants: list[IntentVariant] = field(default_factory=list)
    baseline: ElementInfo | None = None


def build_profile(locator: str, action_type: str = "click", action_name: str = "",
                  baseline: ElementInfo | None = None) -> TargetProfile:
    h = parse_locator_hints(locator)
    action_tokens = tokenize(action_name)
    attr_bits = [h["id"] or ""] + h["classes"] + [v for k, v in h["attrs"].items() if k != "href" or v != "#"]
    text_bits = list(h["texts"]) + [h["attrs"].get("placeholder", ""), h["attrs"].get("aria-label", "")]
    values = [h["id"] or ""] + [v for k, v in h["attrs"].items()
                                if k in ("id", "name", "data-testid", "data-test", "data-qa", "data-cy")]
    variants = [IntentVariant(_weighted(" ".join(text_bits), action_tokens),
                              _weighted(" ".join(attr_bits), action_tokens),
                              " ".join(b for b in text_bits if b), [v for v in values if v])]
    tag = h["tag"]
    if baseline is not None:
        b_attr = [baseline.id, baseline.name, baseline.test_id, " ".join(_stable_classes(baseline)), baseline.type]
        b_text = [baseline.text, baseline.label, baseline.aria_label, baseline.placeholder]
        variants.append(IntentVariant(_weighted(" ".join(b_text), action_tokens),
                                      _weighted(" ".join(b_attr), action_tokens),
                                      " ".join(x for x in b_text if x),
                                      [v for v in (baseline.id, baseline.test_id, baseline.name) if v]))
        tag = tag or baseline.tag
    return TargetProfile(locator, action_type, action_name, tag, h["role"], variants, baseline)


# ---- factors ----------------------------------------------------------------------------
def _element_type(el: ElementInfo, p: TargetProfile) -> float:
    if p.tag and el.tag == p.tag:
        return 1.0
    if p.tag and any(p.tag in fam and el.tag in fam for fam in FAMILIES):
        return 0.6
    return 0.6 if not p.tag and is_compatible(el, p.action_type) else (0.3 if is_compatible(el, p.action_type) else 0.0)


def _accessibility(el: ElementInfo) -> float:
    return 0.4 * bool(el.role) + 0.4 * bool(accessible_name(el)) + 0.2 * bool(el.aria_label or el.label)


def _proximity(el: ElementInfo, base: ElementInfo) -> float:
    strip = lambda path: [re.sub(r":nth-of-type\(\d+\)", "", s) for s in path.split(" > ") if s]
    a, b = strip(base.dom_path), strip(el.dom_path)
    common = 0
    for x, y in zip(a, b):
        if x != y:
            break
        common += 1
    prefix = common / max(len(a), len(b), 1)
    same_slot = el.tag == base.tag and el.sibling_index == base.sibling_index
    return 0.6 * prefix + 0.2 * (el.parent_tag == base.parent_tag) + 0.2 * same_slot


def evidence_factors(el: ElementInfo, p: TargetProfile) -> dict[str, float | None]:
    text_str = _text_side(el)
    text_tokens, attr_tokens = tokenize(text_str), tokenize(_attr_side(el))
    el_all = canonical(text_tokens | attr_tokens)
    best: dict[str, float | None] = {"text_match": None, "attribute_similarity": None, "semantic_similarity": None}

    def keep(key: str, value: float | None) -> None:
        if value is not None and (best[key] is None or value > best[key]):
            best[key] = value

    for v in p.variants:
        if v.text_intent:
            fuzzy = 0.85 * _ratio(v.text_value, text_str) if v.text_value else 0.0
            keep("text_match", max(_coverage(v.text_intent, text_tokens), fuzzy))
        if v.attr_intent:
            fuzzy = max([_ratio(x, y) for x in v.attr_values for y in (el.id, el.test_id, el.name) if y] or [0.0])
            keep("attribute_similarity", max(_coverage(v.attr_intent, attr_tokens), 0.9 * fuzzy))
        everything = {**v.text_intent, **{k: max(w, v.text_intent.get(k, 0.0)) for k, w in v.attr_intent.items()}}
        if everything:
            keep("semantic_similarity", _coverage(_canonical_weights(everything), el_all))
    prox = _proximity(el, p.baseline) if p.baseline is not None else None
    return {**best, "dom_proximity": prox}


def evidence_score(factors: dict[str, float | None]) -> float:
    intent = {k: v for k, v in factors.items() if k in INTENT_WEIGHTS and v is not None}
    if not intent:
        return 0.0
    mean = sum(INTENT_WEIGHTS[k] * v for k, v in intent.items()) / sum(INTENT_WEIGHTS[k] for k in intent)
    base = 0.6 * max(intent.values()) + 0.4 * mean
    prox = factors.get("dom_proximity")
    if prox is not None:
        base += PROXIMITY_BONUS * prox * (1 - base)
    return base


def quality_factors(c: CandidateLocator, p: TargetProfile) -> dict[str, float | None]:
    el = c.element
    visibility = None
    if p.action_type in ("click", "fill", "assert_visible"):
        visibility = 1.0 if el.visible else 0.0
    return {"uniqueness": 1.0 / max(c.match_count, 1), "stability": c.base_priority,
            "element_type": _element_type(el, p), "accessibility": _accessibility(el),
            "visibility": visibility, "enabled": 1.0 if el.enabled else 0.0}


def quality_score(factors: dict[str, float | None]) -> float:
    used = {k: v for k, v in factors.items() if v is not None}
    return sum(QUALITY_WEIGHTS[k] * v for k, v in used.items()) / sum(QUALITY_WEIGHTS[k] for k in used)


# ---- results ----------------------------------------------------------------------------------
@dataclass
class ScoredCandidate:
    candidate: CandidateLocator
    score: float
    evidence: float
    quality: float
    factors: dict[str, float | None]

    def to_dict(self) -> dict:
        d = self.candidate.to_dict()
        d.update(score=round(self.score, 3), evidence=round(self.evidence, 3), quality=round(self.quality, 3),
                 factors={k: (None if v is None else round(v, 3)) for k, v in self.factors.items()})
        return d


@dataclass
class ScoringResult:
    ranked: list[ScoredCandidate]
    confidence: float
    margin: float | None

    @property
    def best(self) -> ScoredCandidate | None:
        return self.ranked[0] if self.ranked else None

    def should_heal(self, threshold: float) -> bool:
        return self.best is not None and self.confidence >= threshold

    def decision(self, threshold: float) -> str:
        return "heal" if self.should_heal(threshold) else "manual_review"

    def reason(self) -> str:
        b = self.best
        if b is None:
            return "No candidate elements were found."
        f = b.factors
        strong = [k.replace("_", " ") for k in ("text_match", "attribute_similarity", "semantic_similarity", "dom_proximity")
                  if (f.get(k) or 0) >= 0.7]
        why = ", ".join(strong) if strong else "no strong intent match"
        extra = "" if b.candidate.unique else "; locator is not unique"
        return f"{b.candidate.strategy} locator on <{b.candidate.element.tag}> chosen for: {why}{extra}"


def score_candidates(candidates: list[CandidateLocator], profile: TargetProfile) -> ScoringResult:
    """Score, rank and compute a confidence for the best candidate."""
    evidence_cache: dict[int, tuple[float, dict]] = {}
    scored: list[ScoredCandidate] = []
    for c in candidates:
        if c.element is None:
            continue
        if c.element.index not in evidence_cache:
            ef = evidence_factors(c.element, profile)
            evidence_cache[c.element.index] = (evidence_score(ef), ef)
        evidence, ef = evidence_cache[c.element.index]
        qf = quality_factors(c, profile)
        quality = quality_score(qf)
        scored.append(ScoredCandidate(c, EVIDENCE_WEIGHT * evidence + QUALITY_WEIGHT * quality, evidence, quality, {**ef, **qf}))
    scored.sort(key=lambda s: (-s.score, s.candidate.element_index, s.candidate.locator))
    if not scored:
        return ScoringResult([], 0.0, None)

    best = scored[0]
    mine = set(best.candidate.element_indexes)
    rival = next((s for s in scored[1:] if not (set(s.candidate.element_indexes) & mine)), None)
    margin = best.score - rival.score if rival else None
    lead = MARGIN_FULL if margin is None else max(0.0, min(margin, MARGIN_FULL))
    confidence = best.score * (0.75 + 0.25 * lead / MARGIN_FULL)
    if not best.candidate.unique:
        confidence = min(confidence, AMBIGUOUS_CAP)
    if best.evidence < LOW_EVIDENCE:
        confidence = min(confidence, NO_EVIDENCE_CAP)
    return ScoringResult(scored, round(confidence, 4), None if margin is None else round(margin, 4))
