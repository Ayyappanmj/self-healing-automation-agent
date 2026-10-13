"""Browser-free test doubles built from the real demo HTML.

FakePage resolves every locator string the generator can produce for the page's elements
(plus nothing else), so a broken locator like `button#login` on v2 matches nothing, exactly
like in a browser. The real-browser tests are the final check of the locator syntax.
"""
from __future__ import annotations

from pathlib import Path

from agent.dom_inspector import parse_html
from agent.locator_generator import generate_candidates

VERSIONS = Path(__file__).resolve().parent.parent / "demo_site" / "versions"
CRASH = "Target page, context or browser has been closed"


def html_of(version: str) -> str:
    return (VERSIONS / f"{version}.html").read_text(encoding="utf-8")


class FakeLocator:
    def __init__(self, page, selector):
        self.page, self.sel = page, selector

    def _els(self):
        if self.page.crashed:
            raise Exception(f"Locator.count: {CRASH}")
        if self.sel in self.page.invalid:
            raise Exception(f'Unexpected token "" while parsing css selector "{self.sel}"')
        return [self.page.by_index[i] for i in self.page.map.get(self.sel, [])]

    def count(self):
        return len(self._els())

    def _one(self, kind):
        if self.page.crashed:
            raise Exception(f"Page.{kind}: {CRASH}")
        els = self._els()
        if not els:
            raise TimeoutError("Timeout 3000ms exceeded.\nCall log: waiting for locator")
        if len(els) > 1:
            raise Exception(f'Locator.{kind}: Error: strict mode violation: locator("{self.sel}") resolved to {len(els)} elements:')
        el = els[0]
        if (self.sel, kind) in self.page.fail_actions:
            raise TimeoutError("Timeout 3000ms exceeded. element intercepts pointer events")
        if kind in ("click", "fill") and not (el.visible and el.enabled):
            raise TimeoutError("Timeout 3000ms exceeded. element is not visible")
        self.page.log.append((kind, self.sel))
        return el

    def click(self, timeout=None): self._one("click")
    def fill(self, value, timeout=None): self._one("fill")
    def inner_text(self, timeout=None): return self._one("inner_text").text
    def wait_for(self, state="visible", timeout=None): self._one("wait_for")

    def evaluate(self, js, timeout=None):
        (el,) = self._els()
        return {"tag": el.tag, "id": el.id, "name": el.name, "testid": el.test_id,
                "aria": el.aria_label, "text": el.text}

    def is_visible(self): return self._els()[0].visible
    def is_enabled(self): return self._els()[0].enabled


class FakePage:
    def __init__(self, html: str, url: str = "http://demo/v2", title: str = "Demo"):
        self.html, self.url, self._title = html, url, title
        self.elements = parse_html(html)
        self.by_index = {e.index: e for e in self.elements}
        self.map = {c.locator: list(c.element_indexes) for c in generate_candidates(self.elements)}
        for e in self.elements:                          # a browser also resolves a bare #id
            if e.id:
                self.map.setdefault(f"#{e.id}", []).append(e.index)
        self.log: list[tuple[str, str]] = []
        self.crashed = False
        self.invalid: set[str] = set()
        self.fail_actions: set[tuple[str, str]] = set()     # (locator, action kind) that always fail
        self.fail_content = False

    @classmethod
    def version(cls, v: str):
        return cls(html_of(v), url=f"http://demo/{v}", title=f"Demo Login {v}")

    def title(self): return self._title

    def content(self):
        if self.fail_content:
            raise Exception(f"Page.content: {CRASH}")
        return self.html

    def evaluate(self, js):
        raise RuntimeError("no browser: attribute-based visibility is used")

    def screenshot(self, path):
        Path(path).write_bytes(b"png")

    def locator(self, selector): return FakeLocator(self, selector)


class FakeManager:
    """Stands in for BrowserManager; goto() switches between fake pages by URL."""
    def __init__(self, pages: dict[str, FakePage]):
        self.pages, self.current, self.restarts = pages, None, 0
        self.goto_calls: list[str] = []

    @property
    def page(self): return self.current

    def goto(self, url):
        self.goto_calls.append(url)
        self.current = self.pages[url]
        if self.current.crashed:
            raise Exception(CRASH)

    def ensure_alive(self):
        self.restarts += 1
        for p in self.pages.values():
            p.crashed = False


class FakeProvider:
    name, model = "fake", "fake-1"

    def __init__(self, *replies):
        self.replies, self.calls = list(replies), []

    def complete(self, system, prompt, timeout_s):
        self.calls.append(prompt)
        r = self.replies.pop(0) if len(self.replies) > 1 else self.replies[0]
        r = r(prompt) if callable(r) else r
        if isinstance(r, Exception):
            raise r
        return r


class RoutedManager:
    """Drop-in for BrowserManager(headless, slow_mo) so agent.runner.main() can run without a browser."""
    instances: list = []
    fail_on_enter: Exception | None = None

    def __init__(self, headless=False, slow_mo=0):
        self.headless, self.slow_mo, self.pages, self.page_ = headless, slow_mo, {}, None
        RoutedManager.instances.append(self)

    def __enter__(self):
        if RoutedManager.fail_on_enter:
            raise RoutedManager.fail_on_enter
        return self

    def __exit__(self, *exc):
        return False

    @property
    def page(self):
        return self.page_

    def goto(self, url):
        version = url.rstrip("/").rsplit("/", 1)[-1]
        version = version if version in ("v1", "v2", "v3", "v4") else "v2"
        self.page_ = self.pages.setdefault(version, FakePage.version(version))
        self.page_.url = url

    def ensure_alive(self):
        pass


def reset_logging():
    """Release the shared log file handlers (needed on Windows before temp folders are deleted)."""
    import logging
    logger = logging.getLogger("healing_agent")
    for h in list(logger.handlers):
        h.close()
        logger.removeHandler(h)
