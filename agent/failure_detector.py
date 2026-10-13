"""Failure detector: turns a failed ActionResult into a classified FailureReport."""
from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any

from browser.actions import SUPPORTED_ACTIONS, Action, ActionResult


class FailureType(str, Enum):
    NOT_FOUND = "locator_not_found"            # 0 elements match -> UI probably changed
    AMBIGUOUS = "locator_ambiguous"            # >1 elements match
    NOT_INTERACTABLE = "element_not_interactable"  # exists but hidden/disabled
    INVALID_SELECTOR = "invalid_selector"      # malformed locator syntax
    BROWSER_CRASH = "browser_crash"
    UNSUPPORTED_ACTION = "unsupported_action"
    UNKNOWN = "unknown"


# Only these are worth healing. Others need a different fix (restart browser,
# fix the test script, wait for the page state, ...).
HEALABLE = {FailureType.NOT_FOUND, FailureType.AMBIGUOUS}

_CRASH = re.compile(
    r"target (page, context or browser )?(has been )?closed|browser has been closed"
    r"|browser has disconnected|target closed",
    re.I,
)
_INVALID = re.compile(
    r"unexpected token|not a valid selector|error while parsing selector"
    r"|unknown engine|invalid selector|syntaxerror",
    re.I,
)
_RESOLVED = re.compile(r"resolved to (\d+) elements", re.I)


@dataclass
class FailureReport:
    failure_type: FailureType
    healable: bool
    action_type: str
    action_name: str
    locator: str
    error: str
    url: str
    title: str
    timestamp: str
    screenshot_path: str | None = None
    matched_count: int | None = None
    duration_ms: float = 0.0

    def to_dict(self) -> dict:
        d = asdict(self)
        d["failure_type"] = self.failure_type.value
        return d

    def summary(self) -> str:
        return f"{self.failure_type.value} on '{self.locator}' ({'healable' if self.healable else 'not healable'})"


def classify_failure(page: Any, action: Action, error: str | None) -> tuple[FailureType, int | None]:
    """Decide why an action failed. Returns (type, number of matched elements or None)."""
    msg = error or ""
    if action.type not in SUPPORTED_ACTIONS:
        return FailureType.UNSUPPORTED_ACTION, None
    if _CRASH.search(msg):
        return FailureType.BROWSER_CRASH, None
    if _INVALID.search(msg):
        return FailureType.INVALID_SELECTOR, None
    if "strict mode violation" in msg.lower():
        m = _RESOLVED.search(msg)
        return FailureType.AMBIGUOUS, int(m.group(1)) if m else None

    # Timeouts look identical for "missing" and "hidden", so ask the page.
    try:
        loc = page.locator(action.locator)
        count = loc.count()
    except Exception as exc:
        text = str(exc)
        if _CRASH.search(text):
            return FailureType.BROWSER_CRASH, None
        if _INVALID.search(text):
            return FailureType.INVALID_SELECTOR, None
        return FailureType.UNKNOWN, None

    if count == 0:
        return FailureType.NOT_FOUND, 0
    if count > 1:
        return FailureType.AMBIGUOUS, count
    try:
        visible = loc.is_visible()
        enabled = loc.is_enabled()
    except Exception:
        return FailureType.UNKNOWN, 1
    if not visible or (action.type in {"click", "fill"} and not enabled):
        return FailureType.NOT_INTERACTABLE, 1
    return FailureType.UNKNOWN, 1


def detect_failure(
    page: Any,
    result: ActionResult,
    screenshot_dir: str | Path | None = None,
) -> FailureReport | None:
    """Return None for a successful action, otherwise a FailureReport."""
    if result.success:
        return None

    ftype, count = classify_failure(page, result.action, result.error)
    now = datetime.now(timezone.utc)
    shot = None
    if screenshot_dir and ftype != FailureType.BROWSER_CRASH:
        shot = _safe_screenshot(page, Path(screenshot_dir), now)

    return FailureReport(
        failure_type=ftype,
        healable=ftype in HEALABLE,
        action_type=result.action.type,
        action_name=result.action.label(),
        locator=result.action.locator,
        error=result.error or "",
        url=_safe(lambda: page.url),
        title=_safe(lambda: page.title()),
        timestamp=now.isoformat(timespec="milliseconds"),
        screenshot_path=shot,
        matched_count=count,
        duration_ms=result.duration_ms,
    )


def _safe(fn) -> str:
    try:
        return str(fn())
    except Exception:
        return ""


def _safe_screenshot(page: Any, directory: Path, now: datetime) -> str | None:
    try:
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / f"failure_{now:%Y%m%d_%H%M%S_%f}.png"
        page.screenshot(path=str(path))
        return str(path)
    except Exception:
        return None  # a missing screenshot must never break detection
