import urllib.error
import urllib.request

import pytest

from demo_site.app import start_server


@pytest.fixture(scope="module")
def base_url():
    server = start_server(port=0)
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()
    server.server_close()


def fetch(url: str) -> tuple[int, str]:
    with urllib.request.urlopen(url, timeout=5) as r:
        return r.status, r.read().decode()


@pytest.mark.parametrize("route", ["/", "/v1", "/v2", "/v3", "/v4"])
def test_routes_serve_html(base_url, route):
    status, body = fetch(base_url + route)
    assert status == 200
    assert "<html" in body


def test_v1_has_original_login_locator(base_url):
    _, html = fetch(base_url + "/v1")
    assert 'id="login"' in html


def test_v2_replaces_id_with_testid(base_url):
    _, html = fetch(base_url + "/v2")
    assert 'id="login"' not in html
    assert 'data-testid="login-button"' in html


@pytest.mark.parametrize("route", ["/v3", "/v4"])
def test_harder_versions_have_no_id_or_testid_on_login(base_url, route):
    _, html = fetch(base_url + route)
    assert 'id="login"' not in html
    assert "login-button" not in html


def test_unknown_route_is_404(base_url):
    with pytest.raises(urllib.error.HTTPError) as exc:
        fetch(base_url + "/nope")
    assert exc.value.code == 404
