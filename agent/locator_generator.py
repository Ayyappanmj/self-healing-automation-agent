"""Candidate locator generator: turns inspected elements into Playwright locator strings.

Every candidate is a string usable with page.locator(...). `base_priority` is a
prior for how stable the strategy usually is (test ids > ids > aria > text > structure).
Phase 7 combines it with how well each element matches the broken locator's intent.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from agent.dom_inspector import TEST_ID_ATTRS, ElementInfo

STRATEGY_PRIORITY = {
    "test_id": 0.95, "id": 0.85, "aria_label": 0.85, "role_name": 0.80, "name_attr": 0.75,
    "label": 0.70, "placeholder": 0.70, "text": 0.65, "href": 0.60,
    "css_class": 0.40, "type_attr": 0.30, "dom_path": 0.25,
}
GENERATED_ID_PRIORITY = 0.40
MAX_TEXT_LEN = 40

FORM_TAGS = {"input", "select", "textarea"}
CLICKABLE_TAGS = {"button", "a", "input", "select", "textarea", "summary"}
CLICKABLE_ROLES = {"button", "link", "checkbox", "radio", "tab", "menuitem", "switch", "option"}
NON_TEXT_INPUT = {"checkbox", "radio", "submit", "button", "reset", "image", "file", "hidden", "range", "color"}
NAME_FROM_LABEL_ROLES = {"textbox", "checkbox", "radio", "combobox", "spinbutton"}

_IDENT = re.compile(r"^[A-Za-z_][\w-]*$")


@dataclass
class CandidateLocator:
    locator: str
    strategy: str
    base_priority: float
    element_index: int
    element_indexes: list[int] = field(default_factory=list)
    match_count: int = 1          # snapshot elements that produce this exact locator
    description: str = ""
    element: ElementInfo | None = field(default=None, repr=False, compare=False)

    @property
    def unique(self) -> bool:
        return self.match_count == 1

    def to_dict(self) -> dict:
        return {"locator": self.locator, "strategy": self.strategy,
                "base_priority": self.base_priority, "element_index": self.element_index,
                "element_indexes": self.element_indexes, "match_count": self.match_count,
                "unique": self.unique, "description": self.description}


# ---- heuristics ---------------------------------------------------------------
def looks_generated_id(value: str) -> bool:
    return bool(
        re.search(r"\d{4,}", value)
        or re.fullmatch(r"[A-Za-z]{0,4}[-_]?[0-9a-fA-F]{8,}", value)
        or re.match(r"[0-9a-f]{8}-[0-9a-f]{4}-", value)
        or value.startswith(":r")
    )


def looks_generated_class(value: str) -> bool:
    return bool(
        re.fullmatch(r"(css|sc|jsx|emotion|svelte|styled)-[\w-]+", value)
        or re.fullmatch(r"[a-z]{0,4}[-_]?(?=[0-9a-f]*\d)[0-9a-f]{4,8}", value)
    )


def looks_dynamic_text(text: str, volatile: tuple[str, ...] | list[str] = ()) -> bool:
    """Text that will probably differ next time: contains a value the test typed, numbers, an email or a price.

    A locator built from such text (for example "Welcome back, alice!") passes today and breaks tomorrow,
    so the generator does not offer it.
    """
    lowered = text.lower()
    if any(len(v) >= 3 and v.lower() in lowered for v in volatile):
        return True
    return bool(re.search(r"\d{2,}", text) or re.search(r"\S+@\S+\.\S+", text) or re.search(r"[$€£]\s?\d", text))


def _q(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"')


def accessible_name(el: ElementInfo) -> str:
    """Approximation of the accessible name Playwright's role= selector uses."""
    if el.aria_label:
        return el.aria_label
    if el.role in NAME_FROM_LABEL_ROLES:
        # A <label> next to an input with no for= is not an accessible name; the browser falls back to the placeholder.
        associated = el.label if el.label_source in ("for", "wrapping", "aria-labelledby") else ""
        return associated or el.placeholder or el.title
    return el.text or el.title


# ---- per element -----------------------------------------------------------------
def _for_element(el: ElementInfo, volatile: tuple[str, ...] | list[str] = ()) -> list[tuple[str, str, float, str]]:
    out: list[tuple[str, str, float, str]] = []
    tag = el.tag

    def add(locator: str, strategy: str, desc: str, prio: float | None = None) -> None:
        out.append((locator, strategy, STRATEGY_PRIORITY[strategy] if prio is None else prio, desc))

    if el.test_id:
        attr = next((k for k in TEST_ID_ATTRS if el.data_attrs.get(k) == el.test_id), "data-testid")
        add(f'[{attr}="{_q(el.test_id)}"]', "test_id", f"{attr} attribute")
    if el.id:
        loc = f"{tag}#{el.id}" if _IDENT.match(el.id) else f'{tag}[id="{_q(el.id)}"]'
        if looks_generated_id(el.id):
            add(loc, "id", "id attribute (looks auto-generated)", GENERATED_ID_PRIORITY)
        else:
            add(loc, "id", "id attribute")
    if el.aria_label:
        add(f'{tag}[aria-label="{_q(el.aria_label)}"]', "aria_label", "aria-label attribute")
    name = accessible_name(el)
    # Only element text can be dynamic; an aria-label, label or placeholder is a deliberate, stable name.
    name_is_text = bool(el.text) and name == el.text
    if el.role and name and not name.endswith("…") and not (name_is_text and looks_dynamic_text(name, volatile)):
        add(f'role={el.role}[name="{_q(name)}"]', "role_name", f"role '{el.role}' with accessible name")
    if el.name:
        add(f'{tag}[name="{_q(el.name)}"]', "name_attr", "name attribute")
    if el.label and tag in FORM_TAGS and '"' not in el.label and not el.label.endswith("…"):
        if el.parent_tag == "label":
            loc = f'xpath=//label[normalize-space(.)="{el.label}"]//{tag}'
        else:
            loc = f'xpath=//label[normalize-space(.)="{el.label}"]/following::{tag}[1]'
        add(loc, "label", "found via its <label> text")
    if el.placeholder:
        add(f'{tag}[placeholder="{_q(el.placeholder)}"]', "placeholder", "placeholder attribute")
    if el.text and tag not in FORM_TAGS and len(el.text) <= MAX_TEXT_LEN and not looks_dynamic_text(el.text, volatile):
        add(f'{tag}:has-text("{_q(el.text)}")', "text", "visible text")
    if tag == "a" and el.href and el.href != "#" and not el.href.lower().startswith("javascript"):
        add(f'a[href="{_q(el.href)}"]', "href", "link target")
    if tag == "input" and el.type:
        add(f'input[type="{_q(el.type)}"]', "type_attr", "input type only (weak)")
    stable = [c for c in el.classes if _IDENT.match(c) and not looks_generated_class(c)][:2]
    if stable:
        add(tag + "".join("." + c for c in stable), "css_class", "stable-looking CSS classes")
    if el.dom_path:
        add(el.dom_path, "dom_path", "position in the DOM tree (fragile)")
    return out


def is_compatible(el: ElementInfo, action_type: str) -> bool:
    """Can this element plausibly be the target of the given action?"""
    if action_type in ("click", "fill") and not (el.visible and el.enabled):
        return False
    if action_type == "click":
        return el.tag in CLICKABLE_TAGS or el.role in CLICKABLE_ROLES
    if action_type == "fill":
        return (el.tag == "input" and el.type not in NON_TEXT_INPUT) or el.tag in {"textarea", "select"}
    return True   # get_text / assert_visible / unknown: any element


def generate_candidates(source: Any, action_type: str | None = None,
                        volatile: tuple[str, ...] | list[str] = ()) -> list[CandidateLocator]:
    """Generate de-duplicated candidates from a DomSnapshot (or a list of ElementInfo).

    `volatile` lists values the test typed; text containing them is never used for a locator.
    match_count counts how many snapshot elements produce the same locator string, so
    unique=False warns about ambiguity. It is an estimate: the validator (Phase 9)
    checks the real page. If the snapshot was truncated, counts may be too low.
    """
    elements: list[ElementInfo] = list(source.elements if hasattr(source, "elements") else source)
    by_index = {e.index: e for e in elements}
    merged: dict[str, CandidateLocator] = {}
    for el in elements:
        for locator, strategy, prio, desc in _for_element(el, volatile):
            cand = merged.get(locator)
            if cand is None:
                merged[locator] = CandidateLocator(locator, strategy, prio, el.index, [el.index], 1, desc, el)
            else:
                cand.element_indexes.append(el.index)
                cand.match_count += 1
    result = []
    for cand in merged.values():
        if action_type is not None:
            eligible = [i for i in cand.element_indexes if is_compatible(by_index[i], action_type)]
            if not eligible:
                continue
            cand.element_index, cand.element = eligible[0], by_index[eligible[0]]
        result.append(cand)
    result.sort(key=lambda c: (-c.base_priority, c.element_index, c.locator))
    return result
