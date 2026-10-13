"""Plain-language explanation of one healing record (a dict from HealingRecord.to_dict())."""
from __future__ import annotations

FAILURE_TEXT = {
    "locator_not_found": "matched no element on the page",
    "locator_ambiguous": "matched more than one element",
    "element_not_interactable": "found an element that is hidden or disabled",
    "invalid_selector": "is not a valid locator",
    "browser_crash": "failed because the browser closed",
}


def pipeline_steps(rec: dict) -> list[tuple[str, str, str]]:
    """(label, state, detail) per pipeline stage. state: done | failed | skipped."""
    steps = [("Failure detected", "done", rec.get("failure_type", ""))]
    if rec.get("status") == "not_healable":
        rest = ["DOM inspected", "Candidates scored", "AI reasoning", "Confidence gate", "Validated", "Retried"]
        return steps + [(label, "skipped", "") for label in rest]
    n = rec.get("dom_elements", 0)
    steps.append(("DOM inspected", "done" if n else "skipped", f"{n} elements" if n else ""))
    cands = rec.get("candidate_locators", [])
    steps.append(("Candidates scored", "done" if cands else "failed", f"{len(cands)} shown" if cands else "none found"))
    llm = rec.get("llm", {}).get("status", "disabled")
    if llm in ("disabled", "not_consulted"):
        steps.append(("AI reasoning", "skipped", "disabled" if llm == "disabled" else "not needed"))
    else:
        steps.append(("AI reasoning", "done" if llm in ("ok", "declined") else "failed", llm))
    if cands:
        conf, thr = rec.get("confidence", 0.0), rec.get("threshold", 0.0)
        steps.append(("Confidence gate", "done" if conf >= thr else "failed", f"{conf:.2f} vs {thr:.2f}"))
    else:
        steps.append(("Confidence gate", "skipped", ""))
    attempts = rec.get("validation_attempts", [])
    if attempts:
        ok = bool(attempts[-1].get("valid"))
        steps.append(("Validated", "done" if ok else "failed", f"{len(attempts)} attempt(s)"))
    else:
        steps.append(("Validated", "skipped", ""))
    retry = rec.get("retry_result", {})
    if retry:
        steps.append(("Retried", "done" if retry.get("success") else "failed", f"{retry.get('attempts', 0)} attempt(s)"))
    else:
        steps.append(("Retried", "skipped", ""))
    return steps


def explain_record(rec: dict) -> str:
    why = FAILURE_TEXT.get(rec.get("failure_type", ""), "failed")
    parts = [f"The step “{rec.get('action_name', '')}” failed because `{rec.get('original_locator', '')}` {why}."]
    if rec.get("status") == "not_healable":
        parts.append("This is not a locator problem, so the agent did not try to heal it.")
        return " ".join(parts)
    cands = rec.get("candidate_locators", [])
    if cands:
        parts.append(f"The agent inspected {rec.get('dom_elements', 0)} page elements, generated candidate locators and "
                     f"scored them. The best was `{rec.get('selected_locator', '')}` ({rec.get('strategy', '')}) with "
                     f"confidence {rec.get('confidence', 0):.2f} against a threshold of {rec.get('threshold', 0):.2f}.")
    llm = rec.get("llm", {})
    status = llm.get("status", "disabled")
    if status == "ok":
        parts.append(f"The AI reasoner ({llm.get('provider', '')}) was consulted and chose `{llm.get('locator', '')}`: "
                     f"{llm.get('reason', '')}")
    elif status in ("declined", "rejected", "bad_json", "timeout", "unavailable", "error"):
        parts.append(f"The AI reasoner was consulted but did not help ({status}); the deterministic result was kept.")
    s = rec.get("status")
    if s == "healed":
        parts.append("The locator was validated in the page (unique, the intended element, visible and enabled), and "
                     "the failed action then succeeded when retried.")
    elif s == "manual_review":
        parts.append("Confidence was below the threshold, so the agent did not act. Manual review is required.")
    elif s == "validation_failed":
        parts.append("No candidate passed validation, so nothing was changed.")
    elif s == "retry_failed":
        parts.append("The locator validated, but the action still failed when retried.")
    elif s == "no_candidates":
        parts.append("No element on the page suits this action.")
    elif s == "error":
        parts.append(rec.get("message", ""))
    return " ".join(p for p in parts if p)
