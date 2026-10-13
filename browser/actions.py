"""Locator executor: runs single actions against a Playwright page."""
from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

SUPPORTED_ACTIONS = {"click", "fill", "get_text", "assert_visible"}


@dataclass(frozen=True)
class Action:
    type: str                     # click | fill | get_text | assert_visible
    locator: str                  # any Playwright selector (CSS, text=, role=...)
    value: str | None = None      # used by fill
    name: str = ""                # human-readable label for logs/UI

    def label(self) -> str:
        return self.name or f"{self.type} {self.locator}"


@dataclass
class ActionResult:
    success: bool
    action: Action
    error: str | None = None      # first line of the underlying error
    duration_ms: float = 0.0
    output: str | None = None     # e.g. text returned by get_text


def execute_action(page: Any, action: Action, timeout_ms: int = 3000) -> ActionResult:
    """Run one action. Never raises for locator problems: returns a failed result.

    Phase 4 classifies the failure (not found / timeout / invalid selector).
    """
    start = time.perf_counter()
    output = None
    try:
        if action.type not in SUPPORTED_ACTIONS:
            raise ValueError(f"Unsupported action type: {action.type}")
        loc = page.locator(action.locator)
        if action.type == "click":
            loc.click(timeout=timeout_ms)
        elif action.type == "fill":
            loc.fill(action.value or "", timeout=timeout_ms)
        elif action.type == "get_text":
            output = loc.inner_text(timeout=timeout_ms)
        elif action.type == "assert_visible":
            loc.wait_for(state="visible", timeout=timeout_ms)
        return ActionResult(True, action, None, _ms(start), output)
    except Exception as exc:  # Playwright raises Error/TimeoutError; ValueError above
        message = str(exc).strip().splitlines()[0] if str(exc).strip() else type(exc).__name__
        return ActionResult(False, action, message, _ms(start), None)


def run_steps(page: Any, actions: list[Action], timeout_ms: int = 3000) -> list[ActionResult]:
    """Run actions in order and stop at the first failure."""
    results: list[ActionResult] = []
    for action in actions:
        result = execute_action(page, action, timeout_ms)
        results.append(result)
        if not result.success:
            break
    return results


def _ms(start: float) -> float:
    return round((time.perf_counter() - start) * 1000, 1)
