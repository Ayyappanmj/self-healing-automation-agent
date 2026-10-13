"""Test Reports page: per-test results and a downloadable report."""
from __future__ import annotations

import pandas as pd
import streamlit as st

from ui.components import status_badge
from ui.exports import build_markdown_report
from ui.metrics import fmt_ms


def render(store) -> None:
    results = store.results
    if not results:
        st.info("No test results yet. Run the demo first.")
        return
    st.download_button("Download report (Markdown)", build_markdown_report(results, store.records),
                       "healing_report.md", "text/markdown")
    for r in reversed(results):
        title = f"Run {r.get('run_id', '?')} | {r['name']} | {r['status'].replace('_', ' ')} | {fmt_ms(r['duration_ms'])}"
        with st.expander(title):
            st.markdown(status_badge(r["status"]), unsafe_allow_html=True)
            if r.get("label"):
                st.caption(r["label"])
            if r["message"]:
                st.warning(r["message"])
            st.dataframe(pd.DataFrame([{
                "Step": s["name"], "Status": s["status"], "Locator used": s["locator_used"],
                "Duration": fmt_ms(s["duration_ms"]), "Output": s["output"] or "", "Note": s["error"]}
                for s in r["steps"]]), hide_index=True)
            if r["crash_restarts"]:
                st.caption(f"Browser restarts: {r['crash_restarts']}")
