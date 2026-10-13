"""Run Automation page and the live run panel (shared with the dashboard's Run Demo button)."""
from __future__ import annotations

import streamlit as st

from agent.scenarios import UI_CHANGES
from ui import runs, settings
from ui.live import LiveLog
from ui.runs import RunOptions


def run_live(store, opts: RunOptions) -> None:
    """Start the runner, stream its events into a live panel, then save the results and refresh."""
    log = LiveLog()
    wait = "Running... a browser window will open" if not opts.headless else "Running headless..."
    with st.status(wait, expanded=True) as status:
        area = st.empty()
        env = runs.build_env(settings.env_overrides())
        for event in runs.stream_events(runs.build_command(opts), env):
            log.feed(event)
            area.markdown(log.markdown())
        status.update(label="Run failed" if log.error else "Run complete", state="error" if log.error else "complete")
    # The runner process saved this run to the database; the pages read it from there.
    st.session_state["last_run"] = {"markdown": log.markdown(), "console": log.console, "error": log.error}
    st.rerun()


def render_last_run() -> None:
    last = st.session_state.get("last_run")
    if not last:
        return
    with st.expander("Last run log", expanded=True):
        st.markdown(last["markdown"])
        if last["error"]:
            st.error(last["error"])
            if "Executable doesn't exist" in last["error"] or "playwright install" in last["error"]:
                st.code("playwright install chromium", language="powershell")
        if last["console"]:
            with st.expander("Runner console output"):
                st.code("\n".join(last["console"]))


def render(store, read_only: bool = False) -> None:
    if read_only:
        st.info("Running automations is disabled in showcase mode (this hosted copy cannot open browsers). "
                "Clone the repository and run `streamlit run app.py` locally to try it.")
        render_last_run()
        return
    st.markdown("Choose what to run. Each changed UI is tested with the **old v1 locators**, so the agent has to heal them.")
    versions = st.multiselect("Changed UI versions", list(UI_CHANGES), default=["v2", "v3"],
                              format_func=lambda v: f"{v}: {UI_CHANGES[v]}")
    c1, c2 = st.columns(2)
    baseline = c1.checkbox("Run a baseline on UI v1 first (the agent learns the original elements)", value=True)
    safety = c2.checkbox("Include the missing-target safety check", value=True)
    headless = c1.checkbox("Run headless (hide the browser window)", value=False)
    slow_mo = c2.slider("Slow motion (ms between browser actions)", 0, 1000, 250, step=50)
    reuse = c1.checkbox("Reuse locators healed in earlier runs (skips healing the second time)", value=False)
    if st.button("Run automation", type="primary", key="run_custom"):
        if not (versions or baseline or safety):
            st.warning("Select at least one thing to run.")
        else:
            run_live(store, RunOptions(tuple(versions), baseline, safety, headless, slow_mo, reuse))
    render_last_run()
