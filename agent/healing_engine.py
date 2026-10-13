"""Healing engine: detect -> inspect -> generate -> score -> (LLM) -> validate -> retry -> record."""
from __future__ import annotations

import time
from dataclasses import asdict, dataclass, field, replace
from typing import Any

from agent.candidate_scorer import LOW_CONFIDENCE_MESSAGE, build_profile, score_candidates
from agent.config import Settings
from agent.dom_inspector import ElementInfo, inspect_dom
from agent.failure_detector import FailureReport
from agent.llm_reasoner import (
    LLMUnavailable, combine, get_provider, reason_about_failure, should_consult_llm,
)
from agent.locator_generator import generate_candidates
from agent.locator_validator import validate_locator
from browser.actions import Action, execute_action

HEALED = "healed"
MANUAL_REVIEW = "manual_review"
VALIDATION_FAILED = "validation_failed"
RETRY_FAILED = "retry_failed"
NO_CANDIDATES = "no_candidates"
NOT_HEALABLE = "not_healable"
ERROR = "error"
SUCCESS_MESSAGE = "Healed locator successfully."
LOGGED_CANDIDATES = 8


@dataclass
class HealingRecord:
    """One healing attempt: everything needed for logs, history and the UI."""
    timestamp: str
    test_name: str
    action: str
    action_name: str
    original_locator: str
    failure_type: str
    failure_reason: str
    url: str = ""
    title: str = ""
    screenshot_path: str | None = None
    dom_elements: int = 0
    candidate_locators: list[dict] = field(default_factory=list)
    selected_locator: str = ""
    strategy: str = ""
    confidence: float = 0.0
    deterministic_confidence: float = 0.0
    threshold: float = 0.0
    source: str = ""
    healing_reason: str = ""
    llm: dict = field(default_factory=dict)
    validation_result: dict = field(default_factory=dict)
    validation_attempts: list[dict] = field(default_factory=list)
    retry_result: dict = field(default_factory=dict)
    status: str = ERROR
    message: str = ""
    execution_time_ms: float = 0.0

    @property
    def healed(self) -> bool:
        return self.status == HEALED

    def to_dict(self) -> dict:
        return asdict(self)


def _first_line(exc: Exception) -> str:
    text = str(exc).strip()
    return text.splitlines()[0] if text else type(exc).__name__


class HealingEngine:
    def __init__(self, settings: Settings, provider: Any = "auto"):
        self.settings = settings
        if isinstance(provider, str) and provider == "auto":
            try:
                provider = get_provider(settings)
            except LLMUnavailable:
                provider = None
        self.provider = provider

    def heal(self, page: Any, action: Action, failure: FailureReport, *, test_name: str = "",
             baseline: ElementInfo | None = None, volatile_values: tuple[str, ...] | list[str] = ()) -> HealingRecord:
        start = time.perf_counter()
        rec = HealingRecord(
            timestamp=failure.timestamp, test_name=test_name, action=action.type, action_name=action.label(),
            original_locator=action.locator, failure_type=failure.failure_type.value,
            failure_reason=failure.error, url=failure.url, title=failure.title,
            screenshot_path=failure.screenshot_path, threshold=self.settings.confidence_threshold,
        )
        try:
            self._run(page, action, failure, baseline, rec, tuple(volatile_values))
        except Exception as exc:                      # e.g. browser crashed mid-healing
            rec.status = ERROR
            rec.message = f"Healing aborted: {type(exc).__name__}: {_first_line(exc)}"
        rec.execution_time_ms = round((time.perf_counter() - start) * 1000, 1)
        return rec

    # ------------------------------------------------------------------
    def _run(self, page: Any, action: Action, failure: FailureReport, baseline: ElementInfo | None,
             rec: HealingRecord, volatile: tuple[str, ...] = ()) -> None:
        s = self.settings
        if not failure.healable:
            rec.status = NOT_HEALABLE
            rec.message = f"'{failure.failure_type.value}' is not a locator problem, so healing was skipped."
            return

        snapshot = inspect_dom(page)                                   # B. capture the current DOM
        rec.dom_elements = snapshot.total_elements
        candidates = generate_candidates(snapshot, action.type, volatile)  # C. candidates
        if not candidates:
            rec.status = NO_CANDIDATES
            rec.message = "No element on the page is compatible with this action."
            return

        profile = build_profile(action.locator, action.type, action.name, baseline)
        scoring = score_candidates(candidates, profile)                # D. score
        rec.candidate_locators = [x.to_dict() for x in scoring.ranked[:LOGGED_CANDIDATES]]
        rec.deterministic_confidence = scoring.confidence

        llm = None                                                     # E. optional LLM
        if self.provider is None:
            rec.llm = {"status": "disabled"}
        elif should_consult_llm(scoring, s.confidence_threshold):
            llm = reason_about_failure(self.provider, profile, scoring, url=failure.url, title=failure.title,
                                       failure_type=failure.failure_type.value, timeout_s=s.llm_timeout_s)
            rec.llm = llm.to_dict(include_text=True)
        else:
            rec.llm = {"status": "not_consulted"}

        proposal = combine(scoring, llm)
        rec.confidence, rec.source, rec.healing_reason = proposal.confidence, proposal.source, proposal.reason
        if proposal.scored is not None:
            rec.selected_locator = proposal.locator
            rec.strategy = proposal.scored.candidate.strategy

        if not proposal.should_heal(s.confidence_threshold):           # confidence gate: do NOT act
            rec.status = MANUAL_REVIEW
            rec.message = LOW_CONFIDENCE_MESSAGE
            return

        # Validate: try the proposed locator, then other locators for the SAME element.
        element = proposal.scored.candidate.element
        same = set(proposal.scored.candidate.element_indexes)
        alternatives = [proposal.scored] + [x for x in scoring.ranked
                                            if x is not proposal.scored and set(x.candidate.element_indexes) & same]
        chosen, validation = None, None
        for alt in alternatives[: max(1, s.max_healing_attempts)]:
            validation = validate_locator(page, alt.candidate.locator, element, action.type)
            rec.validation_attempts.append(validation.to_dict())
            if validation.valid:
                chosen = alt
                break
        rec.validation_result = validation.to_dict() if validation else {}
        if chosen is None:
            rec.status = VALIDATION_FAILED
            rec.message = "No candidate locator passed validation — manual review required."
            return
        rec.selected_locator, rec.strategy = chosen.candidate.locator, chosen.candidate.strategy

        healed_action = replace(action, locator=rec.selected_locator)  # retry the failed action
        result = None
        attempts = max(1, s.action_retries)
        for attempt in range(1, attempts + 1):
            result = execute_action(page, healed_action, s.action_timeout_ms)
            if result.success:
                break
        rec.retry_result = {"success": result.success, "attempts": attempt, "error": result.error,
                            "duration_ms": result.duration_ms, "output": result.output,
                            "locator": rec.selected_locator}
        if result.success:
            rec.status, rec.message = HEALED, SUCCESS_MESSAGE
        else:
            rec.status = RETRY_FAILED
            rec.message = f"Locator validated, but the action still failed: {result.error}"
