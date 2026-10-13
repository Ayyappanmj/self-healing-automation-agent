from scripts import prepush_check as pc


def write(root, rel, text="x"):
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")
    return rel


def kinds(root, files):
    return sorted((f.kind, f.path) for f in pc.scan(root, files))


def test_clean_repository_has_no_findings(tmp_path):
    files = [write(tmp_path, "app.py", "print('hi')"), write(tmp_path, ".env.example", "GEMINI_API_KEY=")]
    assert pc.scan(tmp_path, files) == []


def test_api_keys_and_tokens_are_found(tmp_path):
    f = [write(tmp_path, "a.py", 'KEY = "sk-' + "a" * 30 + '"'), write(tmp_path, "b.txt", "ghp_" + "A" * 36),
         write(tmp_path, "c.txt", "AKIA" + "B" * 16), write(tmp_path, "d.pem", "-----BEGIN RSA PRIVATE KEY-----"),
         write(tmp_path, "e.py", 'api_key = "' + "z" * 24 + '"')]
    found = {(k, p) for k, p in kinds(tmp_path, f)}
    assert {("secret", "a.py"), ("secret", "b.txt"), ("secret", "c.txt"), ("secret", "d.pem"), ("secret", "e.py")} <= found


def test_empty_or_short_values_are_not_secrets(tmp_path):
    f = [write(tmp_path, "x.py", 'api_key = ""\npassword = "short"'), write(tmp_path, ".env.example", "GEMINI_API_KEY=\n")]
    assert pc.scan(tmp_path, f) == []


def test_placeholders_are_found_except_in_files_that_mention_them_on_purpose(tmp_path):
    f = [write(tmp_path, "LICENSE", "Copyright [Your Name]"), write(tmp_path, "README.md", "git clone <your-username>"),
         write(tmp_path, "docs/github-setup.md", "replace [Your Name]")]
    assert kinds(tmp_path, f) == [("placeholder", "LICENSE"), ("placeholder", "README.md")]


def test_private_files_must_not_be_tracked(tmp_path):
    f = [write(tmp_path, ".env", "KEY=1"), write(tmp_path, "data/healing.db", "x"), write(tmp_path, "data/logs/agent.log", "x")]
    assert {k for k, _ in kinds(tmp_path, f)} == {"tracked-private"} and len(kinds(tmp_path, f)) == 3


def test_large_files_are_flagged(tmp_path):
    p = tmp_path / "big.bin"
    p.write_bytes(b"0" * (pc.MAX_FILE_MB * 1024 * 1024 + 1))
    assert [k for k, _ in kinds(tmp_path, ["big.bin"])] == ["large-file"]


def test_binary_files_are_not_text_scanned(tmp_path):
    (tmp_path / "demo.gif").write_bytes(b"GIF89a sk-" + b"a" * 30)
    assert pc.scan(tmp_path, ["demo.gif"]) == []


def test_missing_and_undecodable_files_are_skipped(tmp_path):
    (tmp_path / "weird.txt").write_bytes(b"\xff\xfe\x00bad")
    assert pc.scan(tmp_path, ["weird.txt", "gone.txt"]) == []


def test_fallback_walk_skips_ignored_folders(tmp_path, monkeypatch):
    write(tmp_path, "keep.py")
    write(tmp_path, ".venv/lib/x.py")
    write(tmp_path, "data/healing.db")
    monkeypatch.setattr(pc.subprocess, "run", lambda *a, **k: (_ for _ in ()).throw(OSError("no git")))
    assert pc.tracked_files(tmp_path) == ["keep.py"]


def test_main_exit_codes(monkeypatch, tmp_path):
    import contextlib
    import io
    monkeypatch.setattr(pc, "scan", lambda: [])
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        assert pc.main() == 0
    monkeypatch.setattr(pc, "scan", lambda: [pc.Finding("secret", "a.py", "looks like: x")])
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        assert pc.main() == 1
    assert "[SECRET] a.py" in out.getvalue() and "1 thing(s)" in out.getvalue()


def test_the_checker_and_its_own_tests_may_contain_fake_keys(tmp_path):
    fake = 'KEY = "sk-' + "a" * 30 + '"'
    files = [write(tmp_path, "scripts/prepush_check.py", fake), write(tmp_path, "tests/test_prepush_check.py", fake),
             write(tmp_path, "other.py", fake)]
    assert kinds(tmp_path, files) == [("secret", "other.py")]
