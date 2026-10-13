"""BrowserManager lifecycle and crash recovery, using fake Playwright objects (no browser needed)."""
import pytest

import browser.manager as manager_module
from browser.manager import BrowserManager


class FakePageObj:
    def __init__(self): self.closed, self.visited, self.shots = False, [], []
    def is_closed(self): return self.closed
    def goto(self, url, timeout=None): self.visited.append((url, timeout))
    def screenshot(self, path): self.shots.append(path)


class FakeContext:
    def __init__(self, browser, fail_close=False): self.b, self.closed, self.fail_close = browser, False, fail_close
    def new_page(self):
        self.b.pages_made += 1
        self.b.last_page = FakePageObj()
        return self.b.last_page
    def close(self):
        if self.fail_close:
            raise RuntimeError("already closed")
        self.closed = True


class FakeBrowser:
    def __init__(self, **launch_args):
        self.launch_args, self.connected, self.closed, self.pages_made, self.last_page = launch_args, True, False, 0, None
        self.viewport = None
    def new_context(self, viewport=None):
        self.viewport = viewport
        return FakeContext(self)
    def is_connected(self): return self.connected
    def close(self): self.closed = True


class FakePW:
    instances = []
    def __init__(self): self.stopped, self.browsers = False, []; FakePW.instances.append(self)
    def start(self): return self
    @property
    def firefox(self): return self
    def launch(self, **kw):
        b = FakeBrowser(**kw)
        self.browsers.append(b)
        return b
    def stop(self): self.stopped = True


@pytest.fixture
def pw(monkeypatch):
    FakePW.instances.clear()
    monkeypatch.setattr("browser.manager.sync_playwright", lambda: FakePW())
    return FakePW


def test_start_launches_with_requested_options(pw):
    m = BrowserManager(headless=True, slow_mo=300)
    page = m.start()
    browser = pw.instances[0].browsers[0]
    assert page is m.page and browser.launch_args == {"headless": True, "slow_mo": 300}
    assert browser.viewport == {"width": 1100, "height": 750}


def test_start_does_not_add_headless_flags_in_headed_mode(pw):
    BrowserManager(headless=False).start()
    assert pw.instances[0].browsers[0].launch_args == {"headless": False, "slow_mo": 0}


def test_page_before_start_raises_clear_error():
    with pytest.raises(RuntimeError, match="not started"):
        BrowserManager().page


def test_goto_remembers_the_last_url(pw):
    m = BrowserManager()
    m.start()
    m.goto("http://x/a", timeout_ms=1234)
    assert m.page.visited == [("http://x/a", 1234)] and m._last_url == "http://x/a"


def test_context_manager_stops_everything(pw):
    with BrowserManager() as m:
        browser = pw.instances[0].browsers[0]
        assert m.is_alive()
    assert browser.closed and pw.instances[0].stopped
    with pytest.raises(RuntimeError):
        m.page


def test_stop_is_idempotent_and_swallows_close_errors(pw):
    m = BrowserManager()
    m.start()
    m._context.fail_close = True                      # closing raises, stop() must still finish
    m.stop()
    m.stop()
    assert pw.instances[0].stopped


def test_is_alive_false_when_disconnected_or_page_closed_or_never_started(pw):
    m = BrowserManager()
    assert m.is_alive() is False
    m.start()
    assert m.is_alive() is True
    m.page.closed = True
    assert m.is_alive() is False
    m.page.closed = False
    pw.instances[0].browsers[0].connected = False
    assert m.is_alive() is False


def test_ensure_alive_keeps_a_healthy_browser(pw):
    m = BrowserManager()
    page = m.start()
    assert m.ensure_alive() is page and len(pw.instances) == 1


def test_ensure_alive_restarts_a_crashed_browser_and_returns_to_the_last_page(pw):
    m = BrowserManager()
    m.start()
    m.goto("http://x/login")
    pw.instances[0].browsers[0].connected = False     # simulate a crash
    new_page = m.ensure_alive()
    assert len(pw.instances) == 2 and new_page.visited == [("http://x/login", 10000)]
    assert m.is_alive() and pw.instances[0].stopped


def test_screenshot_creates_folders_and_returns_the_path(pw, tmp_path):
    m = BrowserManager()
    m.start()
    target = tmp_path / "deep" / "dir" / "shot.png"
    assert m.screenshot(target) == str(target)
    assert target.parent.is_dir() and m.page.shots == [str(target)]


def test_is_alive_false_when_the_browser_object_raises(pw):
    m = BrowserManager()
    m.start()
    pw.instances[0].browsers[0].is_connected = lambda: (_ for _ in ()).throw(RuntimeError("driver gone"))
    assert m.is_alive() is False
