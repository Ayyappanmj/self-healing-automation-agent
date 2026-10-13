"""Starts agent.runner as a subprocess and yields its JSON events. No Streamlit needed.

Playwright runs in the subprocess so it never fights Streamlit's event loop, a browser crash
cannot take the UI down, and the real browser window can open on your screen.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator

ROOT = Path(__file__).resolve().parent.parent


@dataclass
class RunOptions:
    versions: tuple[str, ...] = ("v2", "v3")
    baseline: bool = True
    safety: bool = True
    headless: bool = False
    slow_mo: int = 250
    reuse_healed: bool = False


def build_command(opts: RunOptions) -> list[str]:
    cmd = [sys.executable, "-m", "agent.runner", "--versions", ",".join(opts.versions),
           "--headless", "true" if opts.headless else "false", "--slow-mo", str(opts.slow_mo)]
    if not opts.baseline:
        cmd.append("--no-baseline")
    if not opts.safety:
        cmd.append("--no-safety")
    if opts.reuse_healed:
        cmd.append("--reuse-healed")
    return cmd


def inspect_command(target: str) -> list[str]:
    return [sys.executable, "-m", "agent.runner", "--inspect", target]


def build_env(overrides: dict[str, str] | None = None) -> dict[str, str]:
    env = dict(os.environ)
    env.update({k: str(v) for k, v in (overrides or {}).items() if v is not None and v != ""})
    env["PYTHONUTF8"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    return env


def stream_events(cmd: list[str], env: dict[str, str] | None = None, cwd: Path | str = ROOT) -> Iterator[dict]:
    """Yield runner events; non-JSON lines become {'event': 'console'}; ends with {'event': 'exit'}."""
    proc = subprocess.Popen(cmd, cwd=str(cwd), env=env or build_env(), stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace", bufsize=1)
    try:
        assert proc.stdout is not None
        for line in proc.stdout:
            line = line.rstrip("\r\n")
            if not line:
                continue
            try:
                ev = json.loads(line)
                if isinstance(ev, dict) and "event" in ev:
                    yield ev
                    continue
            except ValueError:
                pass
            yield {"event": "console", "line": line}
    finally:
        if proc.poll() is None:
            proc.kill()
        code = proc.wait()
    yield {"event": "exit", "code": code}


def collect_snapshot(events: Iterator[dict]) -> tuple[dict | None, str | None]:
    """From an --inspect run: (snapshot dict, error message)."""
    snapshot, error, console = None, None, []
    for ev in events:
        if ev["event"] == "snapshot":
            snapshot = ev["snapshot"]
        elif ev["event"] == "error":
            error = ev.get("message")
        elif ev["event"] == "console":
            console.append(ev["line"])
        elif ev["event"] == "exit" and ev.get("code") and not error and snapshot is None:
            error = f"Runner exited with code {ev['code']}. " + " | ".join(console[-3:])
    return snapshot, error
