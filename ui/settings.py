"""Settings page. Values apply to runs started from this UI; they are not written to disk."""
from __future__ import annotations

import requests
import streamlit as st

from agent.config import load_settings
from agent.llm_reasoner import GeminiProvider
from storage.database import project_path
from storage.showcase import export_json


def env_overrides() -> dict[str, str]:
    """Settings chosen on this page, as environment variables for the runner subprocess."""
    g = st.session_state.get
    pairs = {
        "CONFIDENCE_THRESHOLD": g("set_threshold"), "MAX_HEALING_ATTEMPTS": g("set_attempts"),
        "ACTION_TIMEOUT_MS": g("set_timeout"), "ACTION_RETRIES": g("set_retries"),
        "LLM_PROVIDER": g("set_provider"), "LLM_TIMEOUT_S": g("set_llm_timeout"),
        "OLLAMA_URL": g("set_ollama_url"), "OLLAMA_MODEL": g("set_ollama_model"),
        "GEMINI_API_KEY": g("set_gemini_key"),
        "GEMINI_MODEL": g("set_gemini_model"),
    }
    return {k: str(v) for k, v in pairs.items() if v not in (None, "")}


def _check_llm(provider: str) -> None:
    g = st.session_state.get
    try:
        if provider == "ollama":
            r = requests.get(g("set_ollama_url", "http://localhost:11434").rstrip("/") + "/api/tags", timeout=3)
            r.raise_for_status()
            names = [m.get("name", "") for m in r.json().get("models", [])]
            st.success("Ollama is reachable. Installed models: " + (", ".join(names) or "none (run `ollama pull llama3.2`)"))
        elif provider == "gemini":
            settings = load_settings()
            api_key = g("set_gemini_key", "") or settings.gemini_api_key
            if not api_key:
                st.error("GEMINI_API_KEY is not set.")
                return
            gemini = GeminiProvider(
                api_key,
                g("set_gemini_model", settings.gemini_model),
                settings.llm_timeout_s,
            )
            gemini.complete("Reply briefly with OK.", "Connection test", settings.llm_timeout_s)
            st.success("Gemini is reachable.")
    except Exception as exc:
        st.error(f"Could not reach the LLM server: {type(exc).__name__}. Deterministic healing still works.")


def _data_section(store, log_dir: str, read_only: bool = False) -> None:
    st.markdown("#### Stored data")
    db = store.db
    counts = db.counts()
    st.caption(f"Database: `{db.path}` ({db.size_bytes() / 1024:.0f} KB) | logs: `{project_path(log_dir)}`")
    c = st.columns(4)
    c[0].metric("Runs", counts["runs"])
    c[1].metric("Test results", counts["test_results"])
    c[2].metric("Healing events", counts["healing_records"])
    c[3].metric("Repaired locators", counts["locator_updates"])
    st.download_button("Export everything (JSON)", export_json(db), "showcase_run.json", "application/json",
                       help="Save as showcase/showcase_run.json to publish a read-only hosted copy (docs/deployment.md)")
    if read_only:
        return
    confirm = st.checkbox("I understand this permanently deletes all stored runs", key="confirm_clear")
    if st.button("Delete all stored runs", key="clear_runs") and confirm:
        store.clear()
        st.success("Stored runs deleted.")
        st.rerun()


def render(store=None, log_dir: str = "data/logs", read_only: bool = False) -> None:
    d = load_settings()
    if read_only:
        st.info("Showcase mode: settings are shown for reference and have no effect, because nothing runs on this copy.")
    st.caption("Defaults come from your .env file. Changes here apply to runs started from this app and are not saved.")
    st.markdown("#### Healing safety")
    c1, c2 = st.columns(2)
    c1.slider("Confidence threshold (below this, healing is refused)", 0.30, 0.99, float(d.confidence_threshold),
              0.01, key="set_threshold")
    c2.number_input("Max locators validated per failure", 1, 5, int(d.max_healing_attempts), key="set_attempts")
    c1.number_input("Action timeout (ms)", 500, 30000, int(d.action_timeout_ms), step=500, key="set_timeout")
    c2.number_input("Retries of the healed action", 1, 5, int(d.action_retries), key="set_retries")

    st.markdown("#### AI reasoner (optional)")
    options = ["none", "ollama", "gemini"]
    provider = st.selectbox("Provider", options, index=options.index(d.llm_provider) if d.llm_provider in options else 0,
                            key="set_provider",
                            help="none = deterministic healing only. ollama = free local model. gemini = Google Gemini API.")
    if provider == "ollama":
        st.text_input("Ollama URL", d.ollama_url, key="set_ollama_url")
        st.text_input("Ollama model", d.ollama_model, key="set_ollama_model")
    elif provider == "gemini":
        st.text_input("Gemini model", d.gemini_model, key="set_gemini_model")
        st.text_input("API key (kept in this session only)", type="password", key="set_gemini_key")
    st.number_input("LLM timeout (s)", 5, 300, int(d.llm_timeout_s), key="set_llm_timeout")
    if provider != "none" and st.button("Test LLM connection", key="test_llm"):
        _check_llm(provider)
    if provider == "none":
        st.info("The agent heals with its deterministic scorer. All demo scenarios work without an LLM.")
    if store is not None:
        _data_section(store, log_dir, read_only)
