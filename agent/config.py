"""Central configuration loaded from environment / .env."""
from __future__ import annotations

import os
from dataclasses import dataclass

from dotenv import load_dotenv


_SUPPORTED_LLM_PROVIDERS = {"none", "ollama", "gemini"}


def _bool(value: str) -> bool:
    return value.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Settings:
    llm_provider: str = "none"
    ollama_url: str = "http://localhost:11434"
    ollama_model: str = "llama3.2"
    gemini_api_key: str = ""
    gemini_model: str = "gemini-3.8-flash"
    confidence_threshold: float = 0.75
    max_healing_attempts: int = 2
    action_timeout_ms: int = 3000
    action_retries: int = 2
    llm_timeout_s: int = 30
    db_path: str = "data/healing.db"
    log_dir: str = "data/logs"
    screenshot_dir: str = "screenshots"
    log_level: str = "INFO"
    reuse_healed: bool = False
    showcase_mode: bool = False
    showcase_file: str = "showcase/showcase_run.json"
    headless: bool = False

    @property
    def llm_enabled(self) -> bool:
        return self.llm_provider in {"ollama", "gemini"}


def load_settings() -> Settings:
    load_dotenv()
    g = os.getenv
    llm_provider = g("LLM_PROVIDER", "none").strip().lower()
    if llm_provider not in _SUPPORTED_LLM_PROVIDERS:
        supported = ", ".join(sorted(_SUPPORTED_LLM_PROVIDERS))
        raise ValueError(f"Unsupported LLM_PROVIDER {llm_provider!r}; expected one of: {supported}")

    return Settings(
        llm_provider=llm_provider,
        ollama_url=g("OLLAMA_URL", "http://localhost:11434"),
        ollama_model=g("OLLAMA_MODEL", "llama3.2"),
        gemini_api_key=g("GEMINI_API_KEY", ""),
        gemini_model=g("GEMINI_MODEL", "gemini-3.8-flash"),
        confidence_threshold=float(g("CONFIDENCE_THRESHOLD", "0.75")),
        max_healing_attempts=int(g("MAX_HEALING_ATTEMPTS", "2")),
        action_timeout_ms=int(g("ACTION_TIMEOUT_MS", "3000")),
        action_retries=int(g("ACTION_RETRIES", "2")),
        llm_timeout_s=int(g("LLM_TIMEOUT_S", "30")),
        db_path=g("DB_PATH", "data/healing.db"),
        log_dir=g("LOG_DIR", "data/logs"),
        screenshot_dir=g("SCREENSHOT_DIR", "screenshots"),
        log_level=g("LOG_LEVEL", "INFO").upper(),
        reuse_healed=_bool(g("REUSE_HEALED_LOCATORS", "false")),
        showcase_mode=_bool(g("SHOWCASE_MODE", "false")),
        showcase_file=g("SHOWCASE_FILE", "showcase/showcase_run.json"),
        headless=_bool(g("HEADLESS", "false")),
    )
