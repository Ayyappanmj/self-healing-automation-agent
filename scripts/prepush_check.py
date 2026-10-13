"""Last look before `git push`: secrets, placeholders, big files, and files that should never be committed.

    python -m scripts.prepush_check

Scans the files Git would commit (uses `git ls-files` when available, otherwise walks the folder while skipping
ignored folders). Exit code 1 if anything needs attention.
"""
from __future__ import annotations

import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SKIP_DIRS = {".git", ".venv", "__pycache__", "htmlcov", ".pytest_cache", "recordings", "data", "screenshots", "node_modules"}
BINARY_SUFFIXES = {".png", ".gif", ".jpg", ".jpeg", ".mp4", ".db", ".zip", ".pyc", ".ico", ".woff", ".woff2"}
MAX_FILE_MB = 5

SECRET_PATTERNS = {
    "Provider API token": re.compile(r"sk-[A-Za-z0-9_\-]{20,}"),
    "GitHub token": re.compile(r"gh[pousr]_[A-Za-z0-9]{30,}"),
    "AWS access key": re.compile(r"AKIA[0-9A-Z]{16}"),
    "Private key block": re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    "Key assigned to a long literal": re.compile(r"(?i)(api[_-]?key|secret|token|password)\s*[:=]\s*['\"][A-Za-z0-9_\-]{20,}['\"]"),
}
PLACEHOLDERS = ["[Your Name]", "<your-username>"]
NEVER_COMMIT = [".env", "data/healing.db", "data/logs"]
SECRET_SCAN_EXEMPT = {"scripts/prepush_check.py", "tests/test_prepush_check.py"}      # they contain fake keys on purpose
PLACEHOLDER_ALLOWED_IN = {"scripts/prepush_check.py", "docs/github-setup.md", "tests/test_prepush_check.py", "tests/test_docs.py"}


@dataclass
class Finding:
    kind: str       # secret | placeholder | large-file | tracked-private
    path: str
    detail: str


def tracked_files(root: Path = ROOT) -> list[str]:
    try:
        out = subprocess.run(["git", "ls-files"], cwd=root, capture_output=True, text=True, check=True, timeout=30)
        return [line for line in out.stdout.splitlines() if line]
    except (OSError, subprocess.SubprocessError):
        return [p.relative_to(root).as_posix() for p in root.rglob("*")
                if p.is_file() and not (set(p.relative_to(root).parts) & SKIP_DIRS)]


def scan(root: Path = ROOT, files: list[str] | None = None) -> list[Finding]:
    findings: list[Finding] = []
    for rel in files if files is not None else tracked_files(root):
        path = root / rel
        if not path.is_file():
            continue
        norm = rel.replace("\\", "/")
        if any(norm == n or norm.startswith(n + "/") for n in NEVER_COMMIT):
            findings.append(Finding("tracked-private", norm, "this file should be git-ignored, not committed"))
        if path.stat().st_size > MAX_FILE_MB * 1024 * 1024:
            findings.append(Finding("large-file", norm, f"{path.stat().st_size / 1e6:.1f} MB (limit {MAX_FILE_MB} MB)"))
        if path.suffix.lower() in BINARY_SUFFIXES:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        for name, pattern in ([] if norm in SECRET_SCAN_EXEMPT else SECRET_PATTERNS.items()):
            if pattern.search(text):
                findings.append(Finding("secret", norm, f"looks like: {name}"))
        if norm not in PLACEHOLDER_ALLOWED_IN:
            for ph in PLACEHOLDERS:
                if ph in text:
                    findings.append(Finding("placeholder", norm, f"still contains {ph}"))
    return findings


def main() -> int:
    findings = scan()
    if not findings:
        print("Pre-push check: nothing found.")
        return 0
    for f in findings:
        print(f"[{f.kind.upper()}] {f.path}: {f.detail}")
    print(f"\n{len(findings)} thing(s) to fix before pushing.")
    return 1


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
