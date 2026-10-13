"""Healing History page: events, the locator repository and log files, all read from storage."""
from __future__ import annotations

import pandas as pd
import streamlit as st

from agent.logging_setup import JSONL_FILE, LOG_FILE, read_tail
from storage.database import project_path
from ui.exports import records_to_csv, records_to_json


def _events(store) -> None:
    records = store.records
    if not records:
        st.info("No healing history yet. Run the demo to create some.")
        return
    c1, c2, c3 = st.columns(3)
    statuses = c1.multiselect("Status", sorted({r["status"] for r in records}))
    tests = c2.multiselect("Test", sorted({r["test_name"] for r in records}))
    strategies = c3.multiselect("Strategy", sorted({r["strategy"] for r in records if r["strategy"]}))
    rows = [r for r in records if (not statuses or r["status"] in statuses) and (not tests or r["test_name"] in tests)
            and (not strategies or r["strategy"] in strategies)]
    st.dataframe(pd.DataFrame([{
        "Run": r.get("run_id"), "Time": r["timestamp"][:19].replace("T", " "), "Test": r["test_name"],
        "Action": r["action_name"], "Old locator": r["original_locator"], "New locator": r["selected_locator"] or "-",
        "Strategy": r["strategy"] or "-", "Confidence": round(r["confidence"], 2), "Status": r["status"],
        "Healing time (ms)": round(r["execution_time_ms"]), "Failure": r["failure_type"]} for r in rows]),
        hide_index=True)
    st.caption(f"{len(rows)} of {len(records)} events, read from the database")
    d1, d2 = st.columns(2)
    d1.download_button("Download JSON", records_to_json(rows), "healing_events.json", "application/json")
    d2.download_button("Download CSV", records_to_csv(rows), "healing_events.csv", "text/csv")


def _repository(store) -> None:
    st.caption("Every locator the agent has repaired. With \"Reuse locators healed in earlier runs\" on, runs use these "
               "instead of healing again.")
    updates = store.db.locator_updates()
    if not updates:
        st.info("Nothing healed yet.")
        return
    st.dataframe(pd.DataFrame([{
        "Test": u["test_name"], "Old locator": u["original_locator"], "New locator": u["new_locator"],
        "Strategy": u["strategy"], "Confidence": round(u["confidence"], 2), "Times healed": u["times_healed"],
        "First healed": u["first_healed_at"][:19].replace("T", " "),
        "Last healed": u["last_healed_at"][:19].replace("T", " ")} for u in updates]), hide_index=True)


def _logs(log_dir: str) -> None:
    directory = project_path(log_dir)
    jsonl, text = directory / JSONL_FILE, directory / LOG_FILE
    st.caption(f"Log files live in `{directory}`")
    c1, c2 = st.columns(2)
    for col, path, name, mime in ((c1, jsonl, "Structured log (JSON Lines)", "application/x-ndjson"),
                                  (c2, text, "Readable log", "text/plain")):
        if path.exists():
            col.download_button(f"Download {name}", path.read_bytes(), path.name, mime)
        else:
            col.caption(f"{name}: not created yet")
    tail = read_tail(text, 200)
    if tail:
        st.markdown("**Latest log lines**")
        st.code(tail, language="text")
    else:
        st.info("No log lines yet. Run the demo.")


def render(store, log_dir: str = "data/logs") -> None:
    sub = st.tabs(["Healing events", "Locator repository", "Logs"])
    with sub[0]:
        _events(store)
    with sub[1]:
        _repository(store)
    with sub[2]:
        _logs(log_dir)
