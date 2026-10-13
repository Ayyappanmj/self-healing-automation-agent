"""Demo scenarios shared by the runner, the UI and the scripts."""
from __future__ import annotations

from dataclasses import dataclass

from browser.actions import Action

LOGIN_STEPS = [
    Action("fill", "#username", "alice", name="Enter username"),
    Action("fill", "#password", "secret", name="Enter password"),
    Action("click", "button#login", name="Click login"),
    Action("get_text", "#welcome", name="Read welcome message"),
]
SAFETY_STEPS = [Action("click", "button#checkout", name="Click checkout")]   # target does not exist
UI_CHANGES = {"v2": "data-testid", "v3": "renamed fields + text", "v4": "hashed classes + labels"}
DEMO_VERSIONS = ("v2", "v3")


@dataclass
class PlanItem:
    label: str
    name: str
    version: str
    steps: list[Action]
    kind: str            # baseline | ui_change | safety
    suite: str = "login flow"


def build_plan(versions=DEMO_VERSIONS, include_baseline: bool = True, include_safety: bool = True) -> list[PlanItem]:
    plan: list[PlanItem] = []
    if include_baseline:
        plan.append(PlanItem("Baseline: UI v1 (locators work)", "login flow @v1", "v1", LOGIN_STEPS, "baseline"))
    for v in versions:
        plan.append(PlanItem(f"UI {v} changed ({UI_CHANGES.get(v, 'changed')}): same test, old locators",
                             f"login flow @{v}", v, LOGIN_STEPS, "ui_change"))
    if include_safety:
        plan.append(PlanItem("Safety check: a missing target must not be guessed", "safety check @v1", "v1",
                             SAFETY_STEPS, "safety", suite="safety"))
    return plan
