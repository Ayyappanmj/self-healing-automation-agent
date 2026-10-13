"""Pre-recording checklist for the demo.

    python -m scripts.demo_preflight            # check everything
    python -m scripts.demo_preflight --reset    # also delete stored runs, logs and failure screenshots (asks first)

Checks that the recording will show real, clean data: browser installed, a visible browser window,
no leftover runs in the dashboard, and an honest LLM situation. It never changes settings.
"""
from __future__ import annotations

import argparse
import importlib.util
import socket
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import requests

from agent.config import Settings, load_settings
from storage.database import Database, project_path

OK, INFO, WARN, FAIL = "ok", "info", "warn", "fail"
REQUIRED_PACKAGES = ("streamlit", "playwright", "requests", "dotenv")
STREAMLIT_PORT = 8501


@dataclass
class Check:
    name: str
    status: str
    detail: str


def check_python(version: tuple[int, int] | None = None) -> Check:
    v = version or sys.version_info[:2]
    if v >= (3, 11):
        return Check("Python", OK, f"{v[0]}.{v[1]}")
    return Check("Python", FAIL, f"{v[0]}.{v[1]} found; this project needs 3.11 or newer")


def check_packages(find_spec: Callable = importlib.util.find_spec) -> Check:
    missing = [p for p in REQUIRED_PACKAGES if find_spec(p) is None]
    if missing:
        return Check("Packages", FAIL, f"missing: {', '.join(missing)}. Run: pip install -r requirements.txt")
    return Check("Packages", OK, "all required packages import")


def _launch_chromium() -> None:
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        p.chromium.launch(headless=True).close()


def check_chromium(launch: Callable[[], None] = _launch_chromium) -> Check:
    try:
        launch()
    except Exception as exc:
        first = (str(exc).strip().splitlines() or [type(exc).__name__])[0][:120]
        return Check("Chromium", FAIL, f"could not start ({first}). Run: playwright install chromium")
    return Check("Chromium", OK, "starts")


def check_window_visible(settings: Settings) -> Check:
    if settings.headless:
        return Check("Browser window", FAIL,
                     "HEADLESS=true in .env: no browser window will open, so there is nothing to record. Set HEADLESS=false")
    return Check("Browser window", OK, "HEADLESS=false: the browser window will be visible")


def check_threshold(settings: Settings) -> Check:
    default = Settings().confidence_threshold
    if settings.confidence_threshold != default:
        return Check("Confidence threshold", WARN,
                     f"{settings.confidence_threshold} (default is {default}). Say so in the caption if you keep it")
    return Check("Confidence threshold", OK, f"{settings.confidence_threshold} (default)")


def check_llm(settings: Settings, get: Callable = requests.get) -> Check:
    if settings.llm_provider == "none":
        return Check("AI reasoning", INFO,
                     "LLM_PROVIDER=none: the AI panel will say 'disabled'. Do not describe the recording as AI-reasoned; "
                     "say the scoring is deterministic and AI is optional (or enable Ollama for a second take)")
    if settings.llm_provider == "ollama":
        try:
            resp = get(settings.ollama_url.rstrip("/") + "/api/tags", timeout=3)
            resp.raise_for_status()
            names = [m.get("name", "") for m in resp.json().get("models", [])]
        except Exception as exc:
            return Check("AI reasoning", WARN,
                         f"Ollama not reachable at {settings.ollama_url} ({type(exc).__name__}). The run would show "
                         "'unavailable'. Start Ollama or set LLM_PROVIDER=none")
        wanted = settings.ollama_model.split(":")[0]
        if not any(n.split(":")[0] == wanted for n in names):
            return Check("AI reasoning", WARN, f"Ollama is running but model '{settings.ollama_model}' is not installed. "
                                              f"Run: ollama pull {settings.ollama_model}")
        return Check("AI reasoning", OK, f"Ollama is reachable and model '{settings.ollama_model}' is installed. "
                                         "Borderline steps will show real model reasoning")
    if settings.llm_provider == "gemini":
        if not settings.gemini_api_key:
            return Check("AI reasoning", WARN, "GEMINI_API_KEY is not set; Gemini reasoning will be unavailable")
        return Check("AI reasoning", INFO, "Gemini is configured; make sure the API key is not visible on screen")
    return Check("AI reasoning", INFO, f"{settings.llm_provider}: make sure no API key or private URL is visible on screen")


def check_storage(settings: Settings) -> Check:
    path = project_path(settings.db_path)
    if not path.exists():
        return Check("Stored runs", OK, "no database yet: the dashboard will start empty")
    counts = Database(path).counts()
    if counts["runs"] or counts["healing_records"]:
        return Check("Stored runs", WARN, f"{counts['runs']} earlier run(s) and {counts['healing_records']} healing event(s) "
                                          "are stored, so the dashboard will not start empty. "
                                          "Run with --reset for a clean recording")
    return Check("Stored runs", OK, "database is empty")


def check_port(port: int = STREAMLIT_PORT, connect: Callable = socket.create_connection) -> Check:
    try:
        connect(("127.0.0.1", port), timeout=0.5).close()
    except OSError:
        return Check("Streamlit port", OK, f"{port} is free")
    return Check("Streamlit port", WARN, f"something already listens on {port}; "
                                         "an old Streamlit may be running (close it before recording)")


def reset_files(settings: Settings, confirm: bool) -> list[str]:
    """Delete stored runs, demo logs and failure screenshots. Touches only those known files."""
    if not confirm:
        raise PermissionError("reset needs explicit confirmation")
    removed: list[str] = []
    db = project_path(settings.db_path)
    log_dir, shots = project_path(settings.log_dir), project_path(settings.screenshot_dir)
    targets = [db, db.with_name(db.name + "-wal"), db.with_name(db.name + "-shm")]
    targets += [p for pattern in ("agent.log*", "healing.jsonl") for p in log_dir.glob(pattern)]
    targets += list(shots.glob("failure_*.png"))
    for path in targets:
        if path.is_file():
            path.unlink()
            removed.append(str(path))
    return removed


def run_checks(settings: Settings) -> list[Check]:
    return [check_python(), check_packages(), check_chromium(), check_window_visible(settings), check_threshold(settings),
            check_llm(settings), check_storage(settings), check_port()]


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--reset", action="store_true", help="delete stored runs, demo logs and failure screenshots first")
    p.add_argument("--yes", action="store_true", help="do not ask for confirmation with --reset")
    args = p.parse_args(argv)
    settings = load_settings()
    if args.reset:
        if args.yes or input("This deletes stored runs, logs and failure screenshots. Type DELETE to continue: ") == "DELETE":
            removed = reset_files(settings, confirm=True)
            print(f"Removed {len(removed)} file(s).")
        else:
            print("Reset cancelled.")
    checks = run_checks(settings)
    marks = {OK: "[ OK ]", INFO: "[INFO]", WARN: "[WARN]", FAIL: "[FAIL]"}
    for c in checks:
        print(f"{marks[c.status]} {c.name}: {c.detail}")
    failed = [c for c in checks if c.status == FAIL]
    print("\nFix the [FAIL] items before recording." if failed else "\nReady to record. See docs/demo-recording.md.")
    return 1 if failed else 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
