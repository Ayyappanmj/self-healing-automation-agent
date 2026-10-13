"""Self-Healing page: the full explanation of one healing event."""
from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import streamlit as st

from ui.components import stage_row, status_badge
from ui.explain import explain_record, pipeline_steps
from ui.runs import ROOT


def _label(i: int, rec: dict) -> str:
    return f"{i + 1}. {rec['timestamp'][11:19]} | {rec['test_name']} | {rec['action_name']} | {rec['status']}"


def render(store) -> None:
    records = store.records
    if not records:
        st.info("No healing events yet. Run the demo first.")
        return
    idx = st.selectbox("Healing event", range(len(records)), index=len(records) - 1,
                       format_func=lambda i: _label(i, records[i]))
    rec = records[idx]

    st.markdown(status_badge(rec["status"]), unsafe_allow_html=True)
    (st.success if rec["status"] == "healed" else st.warning)(rec["message"] or rec["status"])
    stage_row(pipeline_steps(rec))
    st.markdown(explain_record(rec))

    left, right = st.columns(2)
    with left:
        st.markdown("**Broken locator**")
        st.code(rec["original_locator"], language="css")
        st.caption(f"{rec['failure_type']} | {rec['failure_reason']}")
        st.caption(f"Page: {rec['title']} ({rec['url']})")
    with right:
        st.markdown("**Selected locator**")
        st.code(rec["selected_locator"] or "(none)", language="css")
        st.caption(f"strategy: {rec['strategy'] or '-'} | source: {rec['source'] or '-'}")
    st.progress(min(max(rec["confidence"], 0.0), 1.0),
                text=f"Confidence {rec['confidence']:.2f} (deterministic {rec['deterministic_confidence']:.2f}) "
                     f"| threshold {rec['threshold']:.2f}")

    shot = rec.get("screenshot_path")
    if shot and (ROOT / shot).exists():
        with st.expander("Screenshot at the moment of failure"):
            st.image(str(ROOT / shot))

    st.markdown("#### Candidate locators")
    if rec["candidate_locators"]:
        df = pd.DataFrame([{"Locator": c["locator"], "Strategy": c["strategy"], "Score": c["score"],
                            "Evidence": c["evidence"], "Quality": c["quality"], "Unique": c["unique"]}
                           for c in rec["candidate_locators"]])
        st.dataframe(df, hide_index=True)
        with st.expander("Score breakdown of the selected candidate"):
            top = rec["candidate_locators"][0]
            st.json(top["factors"])
    else:
        st.caption("No candidates were generated.")

    st.markdown("#### AI reasoning")
    llm = rec.get("llm", {})
    st.caption(f"status: {llm.get('status', 'disabled')}")
    if llm.get("status") not in ("disabled", "not_consulted", None):
        st.write(f"{llm.get('provider', '')} / {llm.get('model', '')} | {llm.get('latency_ms', 0)} ms "
                 f"| attempts: {llm.get('attempts', 0)}")
        if llm.get("reason"):
            st.write(f"Reason: {llm['reason']}")
        if llm.get("error"):
            st.warning(llm["error"])
        if llm.get("prompt"):
            with st.expander("Exact prompt sent to the model"):
                st.code(llm["prompt"])
        if llm.get("raw_text"):
            with st.expander("Raw model reply"):
                st.code(llm["raw_text"], language="json")
    elif llm.get("status") == "disabled":
        st.caption("No LLM configured: this was healed in deterministic mode. Enable one in Settings.")

    st.markdown("#### Validation and retry")
    if rec["validation_attempts"]:
        st.code(json.dumps(rec["validation_attempts"], indent=2), language="json")
    if rec["retry_result"]:
        st.code(json.dumps(rec["retry_result"], indent=2), language="json")
