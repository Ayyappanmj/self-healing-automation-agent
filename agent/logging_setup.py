"""Structured logs: a human-readable rotating log and a JSON Lines file with one object per healing event."""
from __future__ import annotations

import json
import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path

from storage.database import project_path

LOGGER_NAME = "healing_agent"
LOG_FILE = "agent.log"
JSONL_FILE = "healing.jsonl"


def setup_logging(log_dir: str | Path = "data/logs", level: str = "INFO") -> logging.Logger:
    """Idempotent: calling it twice does not duplicate handlers."""
    logger = logging.getLogger(LOGGER_NAME)
    logger.setLevel(getattr(logging, str(level).upper(), logging.INFO))
    logger.propagate = False
    if not logger.handlers:
        directory = project_path(log_dir)
        directory.mkdir(parents=True, exist_ok=True)
        handler = RotatingFileHandler(directory / LOG_FILE, maxBytes=1_000_000, backupCount=3, encoding="utf-8")
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
        logger.addHandler(handler)
    return logger


def format_record_line(rec: dict) -> str:
    return (f"{rec.get('status', '').upper()} test={rec.get('test_name', '')!r} step={rec.get('action_name', '')!r} "
            f"{rec.get('original_locator', '')} -> {rec.get('selected_locator') or '-'} "
            f"strategy={rec.get('strategy') or '-'} confidence={rec.get('confidence', 0):.2f} "
            f"source={rec.get('source') or '-'} time_ms={rec.get('execution_time_ms', 0):.0f} "
            f"reason={rec.get('healing_reason', '')!r}")


class JsonlLog:
    """Append-only structured log: timestamp, test_name, action, original_locator, failure_reason,
    candidate_locators, selected_locator, confidence, healing_reason, validation_result, retry_result,
    execution_time_ms (every field of a HealingRecord)."""

    def __init__(self, log_dir: str | Path = "data/logs", filename: str = JSONL_FILE):
        self.directory = project_path(log_dir)
        self.directory.mkdir(parents=True, exist_ok=True)
        self.path = self.directory / filename

    def append(self, record: dict) -> None:
        with self.path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")

    def read_all(self) -> list[dict]:
        if not self.path.exists():
            return []
        out = []
        for line in self.path.read_text(encoding="utf-8").splitlines():
            try:
                out.append(json.loads(line))
            except ValueError:
                continue          # a half-written line must not break reading
        return out


def read_tail(path: str | Path, lines: int = 200) -> str:
    p = Path(path)
    if not p.exists():
        return ""
    return "\n".join(p.read_text(encoding="utf-8", errors="replace").splitlines()[-lines:])
