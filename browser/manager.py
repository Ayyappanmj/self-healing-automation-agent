"""Playwright browser lifecycle, with basic crash recovery."""
from __future__ import annotations

from pathlib import Path

from playwright.sync_api import Browser, BrowserContext, Page, sync_playwright


class BrowserManager:
    def __init__(self, headless: bool = False, slow_mo: int = 0):
        self.headless = headless
        self.slow_mo = slow_mo
        self._pw = None
        self._browser: Browser | None = None
        self._context: BrowserContext | None = None
        self._page: Page | None = None
        self._last_url: str | None = None

    # lifecycle -----------------------------------------------------------
    def start(self) -> Page:
        self._pw = sync_playwright().start()
        self._browser = self._pw.firefox.launch(headless=self.headless, slow_mo=self.slow_mo)
        self._context = self._browser.new_context(viewport={"width": 1100, "height": 750})
        self._page = self._context.new_page()
        return self._page

    def stop(self) -> None:
        for closer in (
            lambda: self._context and self._context.close(),
            lambda: self._browser and self._browser.close(),
            lambda: self._pw and self._pw.stop(),
        ):
            try:
                closer()
            except Exception:
                pass  # already closed or crashed
        self._pw = self._browser = self._context = self._page = None

    def __enter__(self) -> "BrowserManager":
        self.start()
        return self

    def __exit__(self, *exc) -> None:
        self.stop()

    # access --------------------------------------------------------------
    @property
    def page(self) -> Page:
        if self._page is None:
            raise RuntimeError("Browser not started. Call start() first.")
        return self._page

    def is_alive(self) -> bool:
        try:
            return bool(self._browser and self._browser.is_connected()) and not self.page.is_closed()
        except Exception:
            return False

    def ensure_alive(self) -> Page:
        """Restart the browser if it crashed and return to the last URL."""
        if not self.is_alive():
            self.stop()
            self.start()
            if self._last_url:
                self.goto(self._last_url)
        return self.page

    # helpers -------------------------------------------------------------
    def goto(self, url: str, timeout_ms: int = 10000) -> None:
        self._last_url = url
        self.page.goto(url, timeout=timeout_ms)

    def screenshot(self, path: str | Path) -> str:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        self.page.screenshot(path=str(path))
        return str(path)
