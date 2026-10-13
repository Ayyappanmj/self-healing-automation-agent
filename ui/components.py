"""Shared look and small UI helpers."""
from __future__ import annotations

import html

import streamlit as st

CSS = """
<style>
.block-container { padding-top: 1.6rem; max-width: 1200px; }
code { font-size: 0.85em; }
.hero { padding: 0.2rem 0 0.6rem 0; }
.hero h1 { font-size: 1.7rem; margin: 0 0 .15rem 0; }
.hero p { color: #5b6472; margin: 0; }
.wwhr { display: grid; grid-template-columns: repeat(4, 1fr); gap: .6rem; margin: .8rem 0 .4rem 0; }
.wwhr div { border: 1px solid #e5e7eb; border-radius: 10px; padding: .55rem .75rem; font-size: .85rem; background: #fafafa; color: #1f2937; }
.wwhr b { display: block; font-size: .7rem; letter-spacing: .06em; color: #2563eb; margin-bottom: .1rem; }
.badge { display: inline-block; padding: 2px 10px; border-radius: 999px; font-size: .78rem; font-weight: 600; }
.badge-ok { background: #dcfce7; color: #166534; }
.badge-warn { background: #fef9c3; color: #854d0e; }
.badge-bad { background: #fee2e2; color: #991b1b; }
.badge-info { background: #dbeafe; color: #1e40af; }
.badge-off { background: #f3f4f6; color: #6b7280; }
.stage { border: 1px solid #e5e7eb; border-radius: 10px; padding: .5rem .6rem; text-align: center; font-size: .78rem; }
.stage b { display: block; font-size: .82rem; }
.stage-done { background: #f0fdf4; border-color: #bbf7d0; color: #14532d; }
.stage-failed { background: #fef2f2; border-color: #fecaca; color: #7f1d1d; }
.stage-skipped { background: #f9fafb; color: #6b7280; }
@media (max-width: 800px) { .wwhr { grid-template-columns: repeat(2, 1fr); } }
</style>
"""

STATUS_BADGE = {"passed": "ok", "healed": "ok", "failed": "bad", "manual_review": "warn", "validation_failed": "warn",
                "retry_failed": "bad", "not_healable": "off", "no_candidates": "warn", "error": "bad", "skipped": "off"}


def inject_css() -> None:
    st.markdown(CSS, unsafe_allow_html=True)


def badge(text: str, kind: str = "info") -> str:
    return f'<span class="badge badge-{kind}">{html.escape(text)}</span>'


def status_badge(status: str) -> str:
    return badge(status.replace("_", " "), STATUS_BADGE.get(status, "info"))


def header() -> None:
    st.markdown(
        """
<div class="hero"><h1>🩹 Self-Healing Automation Agent</h1>
<p>Browser tests that repair their own broken locators.</p></div>
<div class="wwhr">
<div><b>WHAT</b>Self-healing browser automation (Playwright)</div>
<div><b>WHY</b>UI changes break traditional test scripts</div>
<div><b>HOW</b>DOM inspection + candidate generation + AI reasoning + validation</div>
<div><b>RESULT</b>Broken locators recovered automatically when confidence is high enough</div>
</div>""",
        unsafe_allow_html=True,
    )


def stage_row(stages: list[tuple[str, str, str]]) -> None:
    cols = st.columns(len(stages))
    for col, (label, state, detail) in zip(cols, stages):
        icon = {"done": "✓", "failed": "✗", "skipped": "–"}.get(state, "")
        col.markdown(f'<div class="stage stage-{state}"><b>{icon} {html.escape(label)}</b>{html.escape(detail)}</div>',
                     unsafe_allow_html=True)


def showcase_banner(info: dict) -> None:
    when = info.get("exported_at", "")[:10] or "an earlier date"
    st.info(f"**Showcase mode.** You are viewing a real run recorded locally on {when} "
            f"({info.get('results', 0)} tests, {info.get('records', 0)} healing events). Running automations is disabled on this "
            "hosted copy because it cannot open browsers. Clone the repository to run it yourself.")
