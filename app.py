"""Self-Healing Automation Agent: Streamlit dashboard.

Run:  streamlit run app.py
"""
import os
import tempfile
from pathlib import Path

import streamlit as st

from agent.config import load_settings
from storage.database import Database, project_path
from storage.showcase import load_export_file
from ui import components, dashboard, demo, healing, history, inspector, reports, settings
from ui.state import RunStore


@st.cache_resource
def _database(path: str) -> Database:
    return Database(path)


@st.cache_resource
def _showcase_database(file: str):
    """A throwaway database filled from a recorded run. Nothing here is generated: it is an export of a real local run."""
    db = Database(Path(tempfile.mkdtemp()) / "showcase.db")
    info = load_export_file(db, project_path(file))
    return db, info


st.set_page_config(page_title="Self-Healing Automation Agent", page_icon="🩹", layout="wide")
components.inject_css()
try:  # on Streamlit Community Cloud, an app secret SHOWCASE_MODE = "true" turns on the read-only showcase
    if str(st.secrets.get("SHOWCASE_MODE", "")).lower() in ("1", "true", "yes"):
        os.environ["SHOWCASE_MODE"] = "true"
except Exception:  # no secrets configured (the normal local case)
    pass
settings_ = load_settings()
read_only = settings_.showcase_mode
if read_only:
    try:
        showcase_db, showcase_info = _showcase_database(settings_.showcase_file)
    except (OSError, ValueError) as exc:
        st.error(f"Showcase mode is on, but the recording could not be loaded ({type(exc).__name__}: {exc}). "
                 f"Put an export at `{settings_.showcase_file}` (see docs/deployment.md).")
        st.stop()
    store = RunStore(showcase_db)
else:
    store = RunStore(_database(settings_.db_path))
components.header()
if read_only:
    components.showcase_banner(showcase_info)

TABS = ["Dashboard", "Run Automation", "Self-Healing", "DOM Inspector", "Healing History", "Test Reports", "Settings"]
tabs = st.tabs(TABS)
with tabs[0]:
    dashboard.render(store, read_only)
with tabs[1]:
    demo.render(store, read_only)
with tabs[2]:
    healing.render(store)
with tabs[3]:
    inspector.render(read_only)
with tabs[4]:
    history.render(store, settings_.log_dir)
with tabs[5]:
    reports.render(store)
with tabs[6]:
    settings.render(store, settings_.log_dir, read_only)
