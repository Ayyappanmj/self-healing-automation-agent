"""Turns runner events into live markdown and collects results/records. No Streamlit needed."""
from __future__ import annotations


def _ms(v: float | None) -> str:
    return "" if v is None else (f"{v:.0f} ms" if v < 1000 else f"{v / 1000:.1f} s")


class LiveLog:
    def __init__(self) -> None:
        self.lines: list[str] = []
        self.results: list[dict] = []
        self.records: list[dict] = []
        self.console: list[str] = []
        self.error: str | None = None
        self.warnings: list[str] = []
        self.exit_code: int | None = None
        self.finished = False

    def markdown(self) -> str:
        return "\n".join(self.lines)

    def feed(self, ev: dict) -> None:
        handler = getattr(self, "_on_" + str(ev.get("event")), None)
        if handler:
            handler(ev)

    # --- handlers ---------------------------------------------------------
    def _on_run_start(self, ev):
        self.lines.append(f"AI reasoner: **{ev.get('llm', 'none')}** | confidence threshold: **{ev.get('threshold')}**")

    def _on_test_start(self, ev):
        self.lines += ["", f"#### {ev.get('label', ev.get('name'))}", ""]

    def _on_step_done(self, ev):
        o = ev["outcome"]
        if o["status"] == "passed":
            self.lines.append(f"- ✅ {o['name']} — `{o['locator_used']}` ({_ms(o['duration_ms'])})")
        elif o["status"] == "skipped":
            self.lines.append(f"- ⏭ {o['name']} — skipped")

    def _on_failure(self, ev):
        f = ev["failure"]
        self.lines.append(f"- ❌ **{ev['step']}** — `{f['locator']}` failed: `{f['failure_type']}`")

    def _on_healing(self, ev):
        r = ev["record"]
        self.records.append(r)
        pad = "    "
        if r["dom_elements"]:
            top = r["candidate_locators"][0] if r["candidate_locators"] else None
            tail = f"; best candidate `{top['locator']}` ({top['score']:.2f})" if top else ""
            self.lines.append(f"{pad}- 🔍 inspected {r['dom_elements']} elements{tail}")
        if r["selected_locator"]:
            self.lines.append(f"{pad}- 🎯 selected `{r['selected_locator']}` ({r['strategy']}), confidence "
                              f"**{r['confidence']:.2f}** (threshold {r['threshold']:.2f}, source: {r['source']})")
        if r["status"] == "healed":
            self.lines.append(f"{pad}- 🩹 **{r['message']}** Validated, retried OK in {_ms(r['execution_time_ms'])}")
        else:
            self.lines.append(f"{pad}- ⚠️ **{r['message']}**")

    def _on_test_end(self, ev):
        res = ev["result"]
        self.results.append({**res, "label": ev.get("label", ""), "kind": ev.get("kind", "")})
        self.lines += ["", f"**Result: {res['status'].upper().replace('_', ' ')}** in {_ms(res['duration_ms'])}"]

    def _on_error(self, ev):
        self.error = ev.get("message", "unknown error")
        self.lines += ["", f"🛑 **{self.error}**"]

    def _on_warning(self, ev):
        msg = ev.get("message", "")
        self.warnings.append(msg)
        self.lines += ["", f"ℹ️ {msg}"]

    def _on_console(self, ev):
        self.console.append(ev.get("line", ""))

    def _on_exit(self, ev):
        self.exit_code, self.finished = ev.get("code"), True
        if ev.get("code") and self.error is None:
            self.error = f"The runner exited with code {ev['code']}. " + " | ".join(self.console[-3:])
            self.lines += ["", f"🛑 **{self.error}**"]
