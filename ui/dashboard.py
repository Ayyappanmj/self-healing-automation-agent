"""Dashboard: Run Demo button and metrics computed from real runs only."""
from __future__ import annotations

import pandas as pd
import streamlit as st

from ui import demo
from ui.components import status_badge
from ui.metrics import compute_metrics, fmt_ms, fmt_percent, recent_events
from ui.runs import RunOptions


def render(store, read_only: bool = False) -> None:
    left, right = st.columns([1, 4])
    with left:
        run = False if read_only else st.button("▶ Run Demo", type="primary", key="run_demo")
    with right:
        if read_only:
            st.caption("The numbers below come from the recorded run shown in the banner above.")
        else:
            st.caption("Opens a browser, runs a login test on UI v1, re-runs it on changed UIs (v2, v3) using the old "
                       "locators, then tries a target that does not exist, which the agent must refuse to guess.")
    if run:
        demo.run_live(store, RunOptions())
    demo.render_last_run()

    m = compute_metrics(store.results, store.records)
    st.markdown("#### Metrics")
    if not store.results:
        st.info("No runs yet. Click **Run Demo**. These numbers are computed only from runs you execute here."
                if not read_only else "The recording contains no test results.")
    cols = st.columns(6)
    cols[0].metric("Total tests", m["total_tests"])
    cols[1].metric("Successful", m["successful_tests"])
    cols[2].metric("Failed", m["failed_tests"])
    cols[3].metric("Manual review", m["manual_review_tests"])
    cols[4].metric("Healed locators", m["healed_locators"])
    cols[5].metric("Healing success rate", fmt_percent(m["healing_success_rate"]))
    c2 = st.columns(6)
    c2[0].metric("Avg healing time", fmt_ms(m["avg_healing_ms"]))
    c2[1].metric("Healing attempts", m["healing_attempts"])
    st.caption("Successful = passed or healed. Success rate = healed ÷ all healing attempts, so deliberate refusals count "
               "as not healed. Average time covers healed locators only and is measured from the start of healing, so it excludes the failed action's timeout wait. Runs are saved to the local database and survive a page refresh.")

    st.markdown("#### Recent healing events")
    events = recent_events(store.records)
    if not events:
        st.caption("None yet.")
        return
    df = pd.DataFrame([{
        "Time": e["timestamp"][11:19], "Test": e["test_name"], "Step": e["action_name"],
        "Old locator": e["original_locator"], "New locator": e["selected_locator"] or "-",
        "Strategy": e["strategy"] or "-", "Confidence": round(e["confidence"], 2),
        "Status": e["status"].replace("_", " "), "Healing time (ms)": round(e["execution_time_ms"]),
    } for e in events])
    st.dataframe(df, hide_index=True)
