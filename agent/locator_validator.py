"""Locator validator: proves a proposed locator hits exactly the intended element."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from agent.dom_inspector import DomSnapshot, ElementInfo

IDENTITY_JS = """el => ({
  tag: el.tagName.toLowerCase(),
  id: el.id || '',
  name: el.getAttribute('name') || '',
  testid: el.getAttribute('data-testid') || el.getAttribute('data-test-id') || el.getAttribute('data-test')
          || el.getAttribute('data-qa') || el.getAttribute('data-cy') || '',
  aria: el.getAttribute('aria-label') || '',
  text: (el.innerText || el.textContent || ((el.type === 'submit' || el.type === 'button') ? el.value : '') || '')
          .replace(/\\s+/g, ' ').trim().slice(0, 100)
})"""


@dataclass
class ValidationResult:
    locator: str
    valid: bool
    count: int | None = None
    checks: dict[str, bool | None] = field(default_factory=dict)   # None = not applicable
    reason: str = ""
    identity: dict | None = None

    def to_dict(self) -> dict:
        return {"locator": self.locator, "valid": self.valid, "count": self.count,
                "checks": dict(self.checks), "reason": self.reason, "identity": self.identity}


def _norm(s: str) -> str:
    return " ".join((s or "").split())


def same_element(identity: dict, expected: ElementInfo) -> bool:
    """Tag must match, and at least one distinguishing attribute or the text must match."""
    if identity.get("tag") != expected.tag:
        return False
    pairs = [(expected.id, identity.get("id")), (expected.test_id, identity.get("testid")),
             (expected.name, identity.get("name")), (expected.aria_label, identity.get("aria")),
             (_norm(expected.text), _norm(identity.get("text", "")))]
    expected_any = [(e, a) for e, a in pairs if e]
    return True if not expected_any else any(e == a for e, a in expected_any)


def _first_line(exc: Exception) -> str:
    text = str(exc).strip()
    return text.splitlines()[0] if text else type(exc).__name__


def validate_locator(page: Any, locator: str, expected: ElementInfo | None, action_type: str,
                     timeout_ms: int = 1500) -> ValidationResult:
    """Checks: resolves, unique, is the intended element, and has the state the action needs."""
    res = ValidationResult(locator, False)
    try:
        loc = page.locator(locator)
        res.count = loc.count()
    except Exception as exc:
        res.checks["resolves"] = False
        res.reason = f"locator could not be evaluated: {_first_line(exc)}"
        return res
    res.checks["resolves"] = res.count >= 1
    res.checks["unique"] = res.count == 1
    if res.count == 0:
        res.reason = "locator matches no element"
        return res
    if res.count > 1:
        res.reason = f"locator matches {res.count} elements (ambiguous)"
        return res
    try:
        res.identity = loc.evaluate(IDENTITY_JS, timeout=timeout_ms)
        visible, enabled = loc.is_visible(), loc.is_enabled()
    except Exception as exc:
        res.reason = f"could not inspect the matched element: {_first_line(exc)}"
        return res
    res.checks["intended_element"] = same_element(res.identity, expected) if expected is not None else None
    needs_state = action_type in ("click", "fill")
    res.checks["visible"] = visible if (needs_state or action_type == "assert_visible") else None
    res.checks["enabled"] = enabled if needs_state else None
    failed = [k for k, v in res.checks.items() if v is False]
    res.valid = not failed
    res.reason = "all checks passed" if res.valid else "failed checks: " + ", ".join(failed)
    return res


# ---- baseline fingerprints (learned from passing runs) ---------------------------------------
def fingerprint(page: Any, locator: str, timeout_ms: int = 500) -> dict | None:
    """Identity of the element a working locator points at, or None if not exactly one match."""
    try:
        loc = page.locator(locator)
        if loc.count() != 1:
            return None
        return loc.evaluate(IDENTITY_JS, timeout=timeout_ms)
    except Exception:
        return None


def find_element(snapshot: DomSnapshot, identity: dict) -> ElementInfo | None:
    """Match a fingerprint back to a snapshot element. None when absent or ambiguous."""
    best: list[ElementInfo] = []
    best_score = 0
    for el in snapshot.elements:
        if el.tag != identity.get("tag"):
            continue
        score = sum(1 for e, a in [(el.id, identity.get("id")), (el.test_id, identity.get("testid")),
                                    (el.name, identity.get("name")), (el.aria_label, identity.get("aria")),
                                    (_norm(el.text), _norm(identity.get("text", "")))] if e and e == a)
        if score > best_score:
            best, best_score = [el], score
        elif score == best_score and score > 0:
            best.append(el)
    return best[0] if best_score > 0 and len(best) == 1 else None
