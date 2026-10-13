"""DOM inspector: turns the current page into structured, LLM-friendly element records.

Structure (attributes, labels, hierarchy, nearby text) is parsed in Python from
page.content(), so it is deterministic and unit-testable on plain HTML.
Visibility is refined with one small in-browser check; if that check is
unavailable or misaligned, attribute-based visibility is used instead.
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from html.parser import HTMLParser
from typing import Any

MAX_HTML_CHARS = 1_500_000
VOID_TAGS = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link",
             "meta", "param", "source", "track", "wbr"}
IGNORED_TAGS = {"script", "style", "meta", "link", "noscript", "template", "head", "title"}
RELEVANT_TAGS = {"a", "button", "input", "select", "textarea", "label", "p",
                 "h1", "h2", "h3", "h4", "h5", "h6"}
TEST_ID_ATTRS = ("data-testid", "data-test-id", "data-test", "data-qa", "data-cy")
INPUT_ROLES = {"text": "textbox", "email": "textbox", "search": "textbox", "tel": "textbox",
               "url": "textbox", "number": "spinbutton", "checkbox": "checkbox",
               "radio": "radio", "submit": "button", "button": "button",
               "reset": "button", "image": "button", "password": ""}

# One boolean per element under <body>, in document order.
VISIBILITY_JS = """() => Array.from(document.body.querySelectorAll('*')).map(el => {
  const s = getComputedStyle(el);
  return s.display !== 'none' && s.visibility !== 'hidden' && el.getClientRects().length > 0;
})"""


@dataclass
class ElementInfo:
    index: int
    tag: str
    id: str = ""
    classes: list[str] = field(default_factory=list)
    name: str = ""
    type: str = ""
    role: str = ""
    aria_label: str = ""
    test_id: str = ""
    data_attrs: dict[str, str] = field(default_factory=dict)
    placeholder: str = ""
    title: str = ""
    href: str = ""
    text: str = ""
    label: str = ""
    label_source: str = ""      # for | wrapping | aria-labelledby | sibling (sibling is NOT an accessible name)
    nearby_text: str = ""
    path: str = ""
    dom_path: str = ""          # structural CSS path, e.g. "main > form > button:nth-of-type(2)"
    parent_tag: str = ""
    sibling_index: int = 1
    sibling_count: int = 1
    visible: bool = True
    enabled: bool = True

    def to_dict(self) -> dict:
        return asdict(self)

    def describe(self) -> str:
        q = lambda s: json.dumps(s, ensure_ascii=False)
        bits = [f"[{self.index}] <{self.tag}>"]
        for key, val in (("id", self.id), ("testid", self.test_id), ("name", self.name),
                         ("type", self.type), ("role", self.role), ("aria", self.aria_label),
                         ("placeholder", self.placeholder), ("text", self.text),
                         ("label", self.label), ("near", self.nearby_text)):
            if val:
                bits.append(f"{key}={q(val)}")
        if self.classes:
            bits.append("class=" + q(" ".join(self.classes)))
        if not self.visible:
            bits.append("HIDDEN")
        if not self.enabled:
            bits.append("DISABLED")
        bits.append(f"path={self.path}")
        return " ".join(bits)


@dataclass
class DomSnapshot:
    url: str
    title: str
    timestamp: str
    elements: list[ElementInfo]
    total_elements: int          # relevant elements found before the cap
    truncated: bool
    visibility_source: str       # "browser" | "html-attributes"

    def to_dict(self) -> dict:
        d = asdict(self)
        return d

    def compact(self, limit: int = 60) -> str:
        """Short text form for logs and LLM prompts."""
        lines = [e.describe() for e in self.elements[:limit]]
        if len(self.elements) > limit:
            lines.append(f"... {len(self.elements) - limit} more elements")
        return "\n".join(lines)


# ---- HTML tree ---------------------------------------------------------------
class _Node:
    __slots__ = ("index", "tag", "attrs", "parent", "children", "own_parts", "all_parts")

    def __init__(self, index: int, tag: str, attrs: dict[str, str], parent: "_Node | None"):
        self.index, self.tag, self.attrs, self.parent = index, tag, attrs, parent
        self.children: list[_Node] = []
        self.own_parts: list[str] = []
        self.all_parts: list[str] = []

    @property
    def text(self) -> str:
        return _norm("".join(self.all_parts))


class _TreeBuilder(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.nodes: list[_Node] = []      # every element under <body>, document order
        self.stack: list[_Node] = []
        self.in_body = False

    def handle_starttag(self, tag, attrs):
        if tag == "body":
            self.in_body = True
            return
        if not self.in_body:
            return
        parent = self.stack[-1] if self.stack else None
        node = _Node(len(self.nodes), tag, {k: (v or "") for k, v in attrs}, parent)
        if parent:
            parent.children.append(node)
        self.nodes.append(node)
        if tag not in VOID_TAGS:
            self.stack.append(node)

    def handle_endtag(self, tag):
        if tag == "body":
            self.in_body = False
            return
        for i in range(len(self.stack) - 1, -1, -1):
            if self.stack[i].tag == tag:
                del self.stack[i:]
                return

    def handle_data(self, data):
        if not self.in_body or not self.stack or self.stack[-1].tag in ("script", "style"):
            return
        self.stack[-1].own_parts.append(data)
        for node in self.stack:
            node.all_parts.append(data)


def _norm(text: str) -> str:
    return " ".join(text.split())


def _clip(text: str, n: int) -> str:
    return text if len(text) <= n else text[: n - 1] + "…"


# ---- element extraction --------------------------------------------------------
def _attr_hidden(node: _Node) -> bool:
    n: _Node | None = node
    while n:
        style = n.attrs.get("style", "").replace(" ", "").lower()
        if ("hidden" in n.attrs or "display:none" in style or "visibility:hidden" in style
                or (n.tag == "input" and n.attrs.get("type", "").lower() == "hidden")):
            return True
        n = n.parent
    return False


def _role(node: _Node) -> str:
    explicit = node.attrs.get("role", "").split()
    if explicit:
        return explicit[0]
    tag = node.tag
    if tag == "button":
        return "button"
    if tag == "a":
        return "link" if "href" in node.attrs else ""
    if tag == "input":
        return INPUT_ROLES.get(node.attrs.get("type", "text").lower(), "textbox")
    if tag == "textarea":
        return "textbox"
    if tag == "select":
        return "combobox"
    if len(tag) == 2 and tag[0] == "h" and tag[1].isdigit():
        return "heading"
    return ""


def _label_text(node: _Node, by_for: dict[str, str], by_id: dict[str, str], roots: list[_Node]) -> tuple[str, str]:
    """(label text, how it is associated). Only for/wrapping/aria-labelledby give an accessible name."""
    if node.tag not in ("input", "select", "textarea"):
        return "", ""
    if node.attrs.get("id") and node.attrs["id"] in by_for:
        return by_for[node.attrs["id"]], "for"
    n = node.parent
    while n:                                  # wrapping <label>
        if n.tag == "label":
            return n.text, "wrapping"
        n = n.parent
    for ref in node.attrs.get("aria-labelledby", "").split():
        if ref in by_id:
            return by_id[ref], "aria-labelledby"
    sibs = node.parent.children if node.parent else roots      # <label>Text</label><input> without for=: visual only
    i = sibs.index(node)
    if i > 0 and sibs[i - 1].tag == "label":
        return sibs[i - 1].text, "sibling"
    return "", ""


def _path(node: _Node, depth: int = 6) -> str:
    parts = []
    n: _Node | None = node
    while n and len(parts) < depth:
        seg = n.tag
        if n.attrs.get("id"):
            seg += "#" + n.attrs["id"]
        elif n.attrs.get("class"):
            seg += "." + n.attrs["class"].split()[0]
        parts.append(seg)
        n = n.parent
    return " > ".join(reversed(parts))


def _dom_path(node: _Node, roots: list[_Node]) -> str:
    parts = []
    n: _Node | None = node
    while n:
        siblings = n.parent.children if n.parent else roots
        same = [s for s in siblings if s.tag == n.tag]
        parts.append(n.tag + (f":nth-of-type({same.index(n) + 1})" if len(same) > 1 else ""))
        n = n.parent
    return " > ".join(reversed(parts))


def _is_relevant(node: _Node) -> bool:
    if node.tag in IGNORED_TAGS:
        return False
    if node.tag == "input" and node.attrs.get("type", "").lower() == "hidden":
        return False
    a = node.attrs
    return (node.tag in RELEVANT_TAGS or bool(a.get("id")) or bool(a.get("role"))
            or bool(a.get("aria-label")) or any(k in a for k in TEST_ID_ATTRS))


def _build(node: _Node, by_for: dict[str, str], by_id: dict[str, str], roots: list[_Node]) -> ElementInfo:
    a = node.attrs
    tag = node.tag
    is_button_input = tag == "input" and a.get("type", "").lower() in ("submit", "button", "reset")
    text = a.get("value", "") if is_button_input else node.text   # never read other values
    sibs = [s for s in (node.parent.children if node.parent else []) if s.tag == tag]
    near = []
    if node.parent:
        allsibs = node.parent.children
        i = allsibs.index(node)
        for s in (allsibs[i - 1] if i > 0 else None, allsibs[i + 1] if i + 1 < len(allsibs) else None):
            if s is not None and s.text:
                near.append(_clip(s.text, 40))
    label, label_source = _label_text(node, by_for, by_id, roots)
    return ElementInfo(
        index=node.index, tag=tag, id=a.get("id", ""), classes=a.get("class", "").split(),
        name=a.get("name", ""), type=a.get("type", "").lower(), role=_role(node),
        aria_label=a.get("aria-label", ""),
        test_id=next((a[k] for k in TEST_ID_ATTRS if a.get(k)), ""),
        data_attrs={k: v for k, v in a.items() if k.startswith("data-")},
        placeholder=a.get("placeholder", ""), title=a.get("title", ""), href=a.get("href", ""),
        text=_clip(_norm(text), 100), label=_clip(label, 80), label_source=label_source if label else "",
        nearby_text=" | ".join(near), path=_path(node), dom_path=_dom_path(node, roots),
        parent_tag=node.parent.tag if node.parent else "",
        sibling_index=(sibs.index(node) + 1) if node in sibs else 1, sibling_count=max(len(sibs), 1),
        visible=not _attr_hidden(node),
        enabled=("disabled" not in a) and a.get("aria-disabled", "").lower() != "true",
    )


def _parse(html: str) -> tuple[list[ElementInfo], int]:
    """Return (relevant elements, number of elements under <body>)."""
    builder = _TreeBuilder()
    builder.feed(html[:MAX_HTML_CHARS])
    builder.close()
    nodes = builder.nodes
    by_for = {n.attrs["for"]: n.text for n in nodes if n.tag == "label" and n.attrs.get("for")}
    by_id = {n.attrs["id"]: n.text for n in nodes if n.attrs.get("id")}
    roots = [n for n in nodes if n.parent is None]
    return [_build(n, by_for, by_id, roots) for n in nodes if _is_relevant(n)], len(nodes)


def parse_html(html: str) -> list[ElementInfo]:
    """Parse raw HTML (no browser needed). Visibility is attribute-based."""
    return _parse(html)[0]


def inspect_dom(page: Any, max_elements: int = 300) -> DomSnapshot:
    """Snapshot the live page. Raises only if the page content cannot be read."""
    html = page.content()
    elements, node_count = _parse(html)
    source = "html-attributes"
    try:
        flags = page.evaluate(VISIBILITY_JS)
        if isinstance(flags, list) and len(flags) == node_count:
            for el in elements:
                el.visible = bool(flags[el.index])
            source = "browser"
    except Exception:
        pass  # keep attribute-based visibility
    total = len(elements)
    return DomSnapshot(
        url=_safe(lambda: page.url), title=_safe(lambda: page.title()),
        timestamp=datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
        elements=elements[:max_elements], total_elements=total,
        truncated=total > max_elements, visibility_source=source,
    )


def _safe(fn) -> str:
    try:
        return str(fn())
    except Exception:
        return ""
