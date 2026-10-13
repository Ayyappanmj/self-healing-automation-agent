"""Automation controller: runs a test's steps, heals broken locators, restarts after crashes."""
from __future__ import annotations

import time
from dataclasses import dataclass, field, replace
from typing import Any, Callable

from agent.config import Settings
from agent.dom_inspector import ElementInfo, inspect_dom
from agent.failure_detector import FailureType, detect_failure
from agent.healing_engine import HealingEngine, HealingRecord
from agent.locator_validator import find_element, fingerprint
from browser.actions import Action, execute_action


@dataclass
class AutomationTest:
    name: str
    url: str
    steps: list[Action]
    suite: str = ""            # logical test id; baselines are shared across UI versions of a suite


@dataclass
class StepOutcome:
    name: str
    locator_used: str
    status: str                # passed | healed | failed | manual_review | skipped
    error: str = ""
    duration_ms: float = 0.0
    output: str | None = None
    healing: HealingRecord | None = None

    def to_dict(self) -> dict:
        return {"name": self.name, "locator_used": self.locator_used, "status": self.status,
                "error": self.error, "duration_ms": self.duration_ms, "output": self.output,
                "healing_status": self.healing.status if self.healing else None}


@dataclass
class AutomationResult:
    name: str
    status: str                # passed | healed | failed | manual_review
    steps: list[StepOutcome] = field(default_factory=list)
    healing_records: list[HealingRecord] = field(default_factory=list)
    duration_ms: float = 0.0
    message: str = ""
    crash_restarts: int = 0

    def to_dict(self) -> dict:
        return {"name": self.name, "status": self.status, "steps": [s.to_dict() for s in self.steps],
                "duration_ms": self.duration_ms, "message": self.message,
                "crash_restarts": self.crash_restarts, "healing_count": len(self.healing_records)}


class AutomationController:
    def __init__(self, manager: Any, settings: Settings, engine: HealingEngine | None = None, *,
                 learn_baselines: bool = True, screenshot_dir: str | None = "screenshots",
                 on_record: Callable[[HealingRecord], None] | None = None,
                 on_event: Callable[[str, dict], None] | None = None, max_crash_restarts: int = 1):
        self.manager, self.settings = manager, settings
        self.engine = engine or HealingEngine(settings)
        self.learn_baselines, self.screenshot_dir = learn_baselines, screenshot_dir
        self.on_record, self.on_event, self.max_crash_restarts = on_record, on_event, max_crash_restarts
        self.baselines: dict[tuple[str, str], ElementInfo] = {}   # (suite, original locator) -> fingerprint
        self.healed: dict[tuple[str, str], str] = {}              # (test name, original locator) -> new locator

    def _emit(self, kind: str, /, **payload: Any) -> None:
        """Progress events for UIs/logs. A listener problem must never break a test."""
        if self.on_event:
            try:
                self.on_event(kind, payload)
            except Exception:
                pass

    def run_test(self, test: AutomationTest) -> AutomationResult:
        start = time.perf_counter()
        restarts = 0
        while True:
            result = self._run_once(test)
            if result.status != "crashed" or restarts >= self.max_crash_restarts:
                break
            restarts += 1
            self.manager.ensure_alive()                           # browser crash handling
        if result.status == "crashed":
            result.status, result.message = "failed", "Browser crashed and could not be recovered."
        result.crash_restarts = restarts
        result.duration_ms = round((time.perf_counter() - start) * 1000, 1)
        return result

    # ------------------------------------------------------------------
    def _finish(self, test: AutomationTest, res: AutomationResult, index: int, outcome: StepOutcome) -> None:
        res.steps.append(outcome)
        self._emit("step_done", test=test.name, index=index, outcome=outcome.to_dict())

    def _run_once(self, test: AutomationTest) -> AutomationResult:
        res = AutomationResult(test.name, "passed")
        suite = test.suite or test.name
        try:
            self.manager.goto(test.url)
        except Exception:
            res.status = "crashed"
            return res
        timeout = self.settings.action_timeout_ms
        for i, step in enumerate(test.steps):
            locator = self.healed.get((test.name, step.locator), step.locator)
            action = replace(step, locator=locator)
            page = self.manager.page
            self._emit("step_start", test=test.name, step=step.label(), locator=locator, index=i,
                       total=len(test.steps))
            if self.learn_baselines:
                self._learn(page, suite, step.locator, locator)
            result = execute_action(page, action, timeout)
            if result.success:
                self._finish(test, res, i, StepOutcome(step.label(), locator, "passed",
                                                       duration_ms=result.duration_ms, output=result.output))
                continue

            failure = detect_failure(page, result, self.screenshot_dir)
            if failure.failure_type == FailureType.BROWSER_CRASH:
                res.status = "crashed"
                return res
            self._emit("failure", test=test.name, step=step.label(), failure=failure.to_dict())
            record = self.engine.heal(page, action, failure, test_name=test.name,
                                      baseline=self.baselines.get((suite, step.locator)),
                                      volatile_values=[s.value for s in test.steps if s.type == "fill" and s.value])
            res.healing_records.append(record)
            self._emit("healing", test=test.name, step=step.label(), record=record.to_dict())
            if self.on_record:
                self.on_record(record)

            if record.healed:
                self.healed[(test.name, step.locator)] = record.selected_locator
                self._finish(test, res, i, StepOutcome(step.label(), record.selected_locator, "healed",
                                                       duration_ms=record.execution_time_ms,
                                                       output=record.retry_result.get("output"), healing=record))
                continue

            status = "manual_review" if record.status in ("manual_review", "validation_failed") else "failed"
            self._finish(test, res, i, StepOutcome(step.label(), locator, status,
                                                   error=record.message or result.error or "",
                                                   duration_ms=result.duration_ms, healing=record))
            for j, s in enumerate(test.steps[i + 1:], start=i + 1):
                self._finish(test, res, j, StepOutcome(s.label(), s.locator, "skipped"))
            res.status, res.message = status, record.message
            return res

        if any(s.status == "healed" for s in res.steps):
            res.status = "healed"
        return res

    def _learn(self, page: Any, suite: str, original: str, locator: str) -> None:
        """While a locator still works, remember what element it points at (baseline fingerprint)."""
        key = (suite, original)
        if key in self.baselines:
            return
        try:
            fp = fingerprint(page, locator)
            if fp is not None:
                element = find_element(inspect_dom(page), fp)
                if element is not None:
                    self.baselines[key] = element
        except Exception:
            pass
