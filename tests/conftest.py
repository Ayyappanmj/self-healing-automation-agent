import pytest

from demo_site.app import start_server


@pytest.fixture(scope="session")
def browser_manager():
    """ONE headless Chromium for the whole test session (Playwright's sync API allows only one per thread)."""
    from browser.manager import BrowserManager

    manager = BrowserManager(headless=True)
    try:
        manager.start()
    except Exception as exc:
        manager.stop()
        pytest.skip(f"Chromium unavailable: {exc}")
    yield manager
    manager.stop()


@pytest.fixture(scope="session")
def demo_base_url():
    server = start_server(port=0)
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()


@pytest.fixture(scope="session")
def browser_page(browser_manager, demo_base_url):
    """(page, demo-site base URL). Skips if Chromium isn't installed."""
    return browser_manager.page, demo_base_url


def pytest_collection_modifyitems(items):
    """Tag every test that needs a real browser so `pytest -m "not browser"` runs without Chromium."""
    for item in items:
        if {"browser_page", "browser_manager", "live"} & set(getattr(item, "fixturenames", [])):
            item.add_marker(pytest.mark.browser)
