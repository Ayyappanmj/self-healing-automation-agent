"""DOM Inspector page: look at what the agent sees and which locators it would generate."""
from __future__ import annotations

import json

import pandas as pd
import streamlit as st

from agent.dom_inspector import ElementInfo
from agent.locator_generator import generate_candidates
from ui import runs, settings

KEY = "dom_snapshot"


def _inspect(target: str) -> None:
    with st.spinner("Opening the page headlessly and reading its DOM..."):
        snap, err = runs.collect_snapshot(runs.stream_events(runs.inspect_command(target),
                                                             runs.build_env(settings.env_overrides())))
    if err:
        st.error(err)
    else:
        st.session_state[KEY] = snap


def render(read_only: bool = False) -> None:
    if read_only:
        st.info("The DOM inspector needs a browser, so it is disabled in showcase mode. Run the app locally to use it.")
        return
    c1, c2 = st.columns([2, 3])
    version = c1.selectbox("Demo UI version", ["v1", "v2", "v3", "v4"], index=1)
    if c1.button("Inspect demo page", key="inspect_demo"):
        _inspect(version)
    url = c2.text_input("...or any URL", placeholder="https://example.com")
    if c2.button("Inspect URL", key="inspect_url") and url.strip():
        _inspect(url.strip())

    snap = st.session_state.get(KEY)
    if not snap:
        st.info("Pick a page and click Inspect. Only structure is read; typed values are never captured.")
        return
    st.caption(f"{snap['title']} | {snap['url']} | {snap['total_elements']} relevant elements "
               f"| visibility from {snap['visibility_source']}" + (" | truncated" if snap["truncated"] else ""))
    elements = [ElementInfo(**e) for e in snap["elements"]]

    f1, f2, f3 = st.columns(3)
    tags = f1.multiselect("Tag", sorted({e.tag for e in elements}))
    text = f2.text_input("Search text / id / label").lower().strip()
    visible_only = f3.checkbox("Visible only", value=False)
    shown = [e for e in elements
             if (not tags or e.tag in tags) and (not visible_only or e.visible)
             and (not text or text in json.dumps(e.to_dict()).lower())]
    st.dataframe(pd.DataFrame([{
        "#": e.index, "Tag": e.tag, "id": e.id, "data-testid": e.test_id, "Role": e.role, "Text": e.text,
        "Label": e.label, "Visible": e.visible, "Enabled": e.enabled, "Path": e.path} for e in shown]), hide_index=True)

    if not shown:
        return
    st.markdown("#### Locators the agent would generate")
    pick = st.selectbox("Element", [e.index for e in shown],
                        format_func=lambda i: next(x.describe()[:110] for x in shown if x.index == i))
    cands = [c for c in generate_candidates(elements) if pick in c.element_indexes]
    st.dataframe(pd.DataFrame([{"Locator": c.locator, "Strategy": c.strategy, "Stability prior": c.base_priority,
                                "Unique": c.unique} for c in cands]), hide_index=True)
    with st.expander("Element as compact text (what the LLM sees)"):
        st.code(next(x.describe() for x in shown if x.index == pick))
    with st.expander("Raw snapshot JSON"):
        st.code(json.dumps(snap, indent=2)[:60000], language="json")
