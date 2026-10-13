from pathlib import Path

from agent.dom_inspector import inspect_dom, parse_html

VERSIONS = Path(__file__).resolve().parent.parent / "demo_site" / "versions"


def load(version: str) -> dict[int, object]:
    return {e.index: e for e in parse_html((VERSIONS / f"{version}.html").read_text(encoding="utf-8"))}


def find(elements, **attrs):
    for e in elements.values():
        if all(getattr(e, k) == v for k, v in attrs.items()):
            return e
    raise AssertionError(f"no element with {attrs}")


# --- demo pages ---------------------------------------------------------------
def test_v1_login_button_details():
    btn = find(load("v1"), id="login")
    assert btn.tag == "button" and btn.text == "Login" and btn.role == "button"
    assert btn.type == "submit" and btn.visible and btn.enabled
    assert btn.path.endswith("form > button#login")
    assert btn.parent_tag == "form"


def test_v1_inputs_have_labels_and_roles():
    user = find(load("v1"), id="username")
    assert user.label == "Username" and user.placeholder == "Username" and user.role == "textbox"
    assert find(load("v1"), id="password").role == ""   # password has no ARIA role


def test_wrapping_label_is_found_for_checkbox():
    assert find(load("v1"), id="remember").label == "Remember me"


def test_v1_welcome_message_is_hidden():
    assert find(load("v1"), id="welcome").visible is False


def test_v2_uses_testid_and_previous_sibling_label():
    els = load("v2")
    btn = find(els, test_id="login-button")
    assert btn.id == "" and btn.text == "Login"
    assert find(els, test_id="username-input").label == "Username"   # <label> without for=
    assert find(els, test_id="welcome-message").visible is False


def test_v3_aria_label_classes_and_email_role():
    els = load("v3")
    btn = find(els, aria_label="Sign in")
    assert btn.classes == ["btn", "btn-primary"] and btn.text == "Sign in"
    email = find(els, name="email")
    assert email.type == "email" and email.role == "textbox" and email.label == "Email address"
    assert find(els, text="Reset password").role == "link"


def test_v4_label_for_and_hashed_classes():
    els = load("v4")
    assert find(els, id="f-a1").label == "Email"
    btn = find(els, text="Log in")
    assert btn.tag == "button" and "c-77b1" in btn.classes
    assert find(els, text="Trouble signing in?").tag == "a"


def test_old_locator_target_has_no_match_after_change():
    assert not any(e.id == "login" for e in load("v2").values())
    assert not any(e.id == "login" for e in load("v3").values())


# --- parser behaviour ------------------------------------------------------------
def test_scripts_styles_and_hidden_inputs_are_ignored():
    html = ("<body><script id=s>var a=1</script><style>p{}</style>"
            "<input type=hidden name=csrf><button>Go</button></body>")
    els = parse_html(html)
    assert [e.tag for e in els] == ["button"]


def test_input_values_are_never_captured():
    els = parse_html('<body><input id=pw type=password value="hunter2"><input type=submit value="Send"></body>')
    assert "hunter2" not in str([e.to_dict() for e in els])
    assert els[1].text == "Send"           # button-like input text comes from value


def test_hidden_state_is_inherited_from_ancestors():
    els = parse_html('<body><div style="display: none"><button id=a>A</button></div>'
                     "<button id=b disabled>B</button></body>")
    a, b = els[0], els[1]
    assert a.visible is False and b.visible is True and b.enabled is False


def test_aria_labelledby_gives_label():
    els = parse_html('<body><span id=l>Full name</span><input id=n aria-labelledby=l></body>')
    assert [e for e in els if e.id == "n"][0].label == "Full name"


def test_sibling_index_counts_same_tag_siblings():
    els = parse_html("<body><ul><a href=#>x</a><a href=#>y</a></ul></body>")
    assert [(e.sibling_index, e.sibling_count) for e in els] == [(1, 2), (2, 2)]


def test_malformed_html_does_not_crash():
    els = parse_html("<body><div><p>text</div><button>ok</body>")
    assert any(e.tag == "button" for e in els)


def test_empty_html_gives_no_elements():
    assert parse_html("") == []


# --- inspect_dom with a fake page ---------------------------------------------------
HTML = '<html><body><button id="a">A</button><p hidden id="m">msg</p><div>x</div></body></html>'


class FakePage:
    url, _t = "http://demo/x", "T"

    def __init__(self, flags="ok", html=HTML):
        self.flags, self.html = flags, html

    def title(self): return self._t
    def content(self): return self.html

    def evaluate(self, js):
        if self.flags == "raise":
            raise RuntimeError("evaluate failed")
        return [True, False, True] if self.flags == "ok" else [True]


def test_inspect_dom_uses_browser_visibility_when_aligned():
    snap = inspect_dom(FakePage("ok"))
    assert snap.visibility_source == "browser"
    assert {e.id: e.visible for e in snap.elements if e.id} == {"a": True, "m": False}
    assert snap.url == "http://demo/x" and snap.title == "T"


def test_inspect_dom_falls_back_when_flags_misaligned():
    snap = inspect_dom(FakePage("short"))
    assert snap.visibility_source == "html-attributes"
    assert {e.id: e.visible for e in snap.elements if e.id}["m"] is False   # from hidden attr


def test_inspect_dom_falls_back_when_evaluate_raises():
    assert inspect_dom(FakePage("raise")).visibility_source == "html-attributes"


def test_max_elements_truncates_and_reports():
    html = "<body>" + "".join(f"<button id=b{i}>x</button>" for i in range(10)) + "</body>"
    snap = inspect_dom(FakePage("raise", html), max_elements=4)
    assert len(snap.elements) == 4 and snap.total_elements == 10 and snap.truncated


def test_compact_and_dict_output():
    snap = inspect_dom(FakePage("ok"))
    text = snap.compact(limit=1)
    assert '<button>' in text and 'id="a"' in text and "more elements" in text
    d = snap.to_dict()
    assert d["elements"][0]["tag"] == "button"


# --- real browser ---------------------------------------------------------------------
def test_real_browser_visibility_aligns_and_updates(browser_page):
    from browser.actions import Action, run_steps
    page, base = browser_page
    page.goto(base + "/v2")
    snap = inspect_dom(page)
    assert snap.visibility_source == "browser"          # proves index alignment
    btn = next(e for e in snap.elements if e.test_id == "login-button")
    assert btn.visible and btn.text == "Login"
    welcome = next(e for e in snap.elements if e.test_id == "welcome-message")
    assert welcome.visible is False

    run_steps(page, [Action("fill", '[data-testid="username-input"]', "a"),
                     Action("fill", '[data-testid="password-input"]', "b"),
                     Action("click", '[data-testid="login-button"]')], timeout_ms=2000)
    after = inspect_dom(page)
    assert next(e for e in after.elements if e.test_id == "welcome-message").visible is True


def test_real_browser_v4_snapshot_has_button_text(browser_page):
    page, base = browser_page
    page.goto(base + "/v4")
    snap = inspect_dom(page)
    assert any(e.tag == "button" and e.text == "Log in" for e in snap.elements)
