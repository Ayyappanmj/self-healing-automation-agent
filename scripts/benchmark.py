"""Measure the agent on the bundled demo site. Every number in the output is computed from these runs.

    python -m scripts.benchmark                 # ~5 minutes, deterministic mode (no LLM)
    python -m scripts.benchmark --quick         # 1 repetition, for a fast check
    python -m scripts.benchmark --llm           # also use the LLM configured in .env

Design (so the numbers mean something):
- Ground truth: for every UI version we know which element each step is meant to hit. After a heal we check the
  healed locator points at the SAME DOM node as the ground-truth locator, so a heal onto the wrong element is counted
  as wrong, not as a success.
- One broken step per trial: earlier steps use correct locators, then exactly one old (v1) locator is run.
- Negatives: targets that do not exist (button#checkout) or were removed (the remember-me box on v4). The agent must refuse.
- Two modes: without a baseline (only the broken locator + step name) and with a baseline learned from a passing run on v1.
- Flows: the whole login flow with every old locator, to measure failed-test recovery and execution overhead against the
  same flow written with correct locators. A control run with healing disabled shows what fails without the agent.
Scope: this is a 3-version demo site. The results describe it, not real-world applications.
"""
from __future__ import annotations

import argparse
import importlib.metadata
import json
import platform
import statistics
import sys
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from agent.config import Settings, load_settings
from agent.controller import AutomationController, AutomationTest
from agent.healing_engine import HealingEngine
from browser.actions import Action
from browser.manager import BrowserManager
from demo_site.app import start_server

VERSIONS = ("v2", "v3", "v4")
FLOW_KEYS = ("username", "password", "login", "welcome", "forgot")
OLD = {
    "username": Action("fill", "#username", "alice", name="Enter username"),
    "password": Action("fill", "#password", "secret", name="Enter password"),
    "remember": Action("click", "#remember", name="Click remember me"),
    "login": Action("click", "button#login", name="Click login"),
    "welcome": Action("get_text", "#welcome", name="Read welcome message"),
    "forgot": Action("click", "a#forgot", name="Click forgot password"),
}
CHECKOUT = Action("click", "button#checkout", name="Click checkout")
# Ground truth: which element each step means in each UI version. "" = the element does not exist in that version.
TRUTH = {
    "v2": {"username": '[data-testid="username-input"]', "password": '[data-testid="password-input"]',
           "remember": '[data-testid="remember-checkbox"]', "login": '[data-testid="login-button"]',
           "welcome": '[data-testid="welcome-message"]', "forgot": '[data-testid="forgot-link"]'},
    "v3": {"username": 'input[name="email"]', "password": 'input[name="pass"]', "remember": 'input[name="keep"]',
           "login": 'button[aria-label="Sign in"]', "welcome": "#flash", "forgot": "a.link-muted"},
    "v4": {"username": "#f-a1", "password": "#f-b2", "remember": "", "login": "button.c-77b1",
           "welcome": "p.c-msg", "forgot": "a.c-3e90"},
}
PREFIX = {"welcome": ("username", "password", "login")}      # the message only appears after logging in
CASE_KEYS = ("username", "password", "remember", "login", "welcome", "forgot")
LEARN_STEPS = [OLD[k] for k in ("username", "password", "remember", "login", "welcome", "forgot")]


def truth_action(key: str, version: str) -> Action:
    return replace(OLD[key], locator=TRUTH[version][key])


def positive_cases() -> list[tuple[str, str]]:
    return [(k, v) for v in VERSIONS for k in CASE_KEYS if TRUTH[v][k]]


def negative_cases() -> list[tuple[str, str]]:
    removed = [(k, v) for v in VERSIONS for k in CASE_KEYS if not TRUTH[v][k]]
    return removed + [("checkout", v) for v in VERSIONS]


def same_dom_node(page: Any, locator_a: str, locator_b: str) -> bool:
    """True only if both locators resolve to the identical DOM node."""
    try:
        a = page.locator(locator_a).element_handle(timeout=1500)
        b = page.locator(locator_b).element_handle(timeout=1500)
        return bool(a.evaluate("(x, y) => x === y", b))
    except Exception:
        return False


# ---- running ---------------------------------------------------------------------------------------
def _trial(controller: AutomationController, manager: Any, base: str, name: str, version: str, steps: list[Action]):
    events: list[tuple[str, dict]] = []
    controller.on_event = lambda kind, payload: events.append((kind, payload))
    result = controller.run_test(AutomationTest(name, f"{base}/{version}", steps, "bench"))
    wait_ms = next((p["failure"]["duration_ms"] for k, p in events if k == "failure"), 0.0)
    return result, wait_ms


def run_positive(controller, manager, base, key, version, rep, mode) -> dict:
    steps = [truth_action(k, version) for k in PREFIX.get(key, ())] + [OLD[key]]
    result, wait_ms = _trial(controller, manager, base, f"bench {mode} {key}@{version}#{rep}", version, steps)
    last = result.steps[-1]
    record = result.healing_records[-1] if result.healing_records else None
    if last.status == "healed":
        right = same_dom_node(manager.page, last.locator_used, TRUTH[version][key])
        outcome = "healed_correct" if right else "healed_wrong"
    elif last.status == "manual_review":
        outcome = "refused"
    else:
        outcome = "failed"
    return {"kind": "positive", "mode": mode, "case": key, "version": version, "rep": rep, "outcome": outcome,
            "healed_locator": last.locator_used if last.status == "healed" else "",
            "confidence": record.confidence if record else None, "strategy": record.strategy if record else "",
            "heal_ms": record.execution_time_ms if record else None, "wait_ms": wait_ms,
            "recovery_ms": (wait_ms + record.execution_time_ms) if record and last.status == "healed" else None}


def run_negative(controller, manager, base, key, version, rep, mode) -> dict:
    action = CHECKOUT if key == "checkout" else OLD[key]
    steps = [truth_action(k, version) for k in PREFIX.get(key, ())] + [action]
    result, _ = _trial(controller, manager, base, f"bench {mode} neg {key}@{version}#{rep}", version, steps)
    last = result.steps[-1]
    outcome = {"healed": "false_heal", "manual_review": "correct_refusal"}.get(last.status, "failed")
    record = result.healing_records[-1] if result.healing_records else None
    return {"kind": "negative", "mode": mode, "case": key, "version": version, "rep": rep, "outcome": outcome,
            "confidence": record.confidence if record else None}


def run_flow(controller, manager, base, version, rep, mode, label: str, steps: list[Action]) -> dict:
    result, _ = _trial(controller, manager, base, f"bench {mode} {label} flow@{version}#{rep}", version, steps)
    return {"kind": "flow", "label": label, "mode": mode, "version": version, "rep": rep, "status": result.status,
            "duration_ms": result.duration_ms, "healed_steps": sum(1 for s in result.steps if s.status == "healed")}


def run_benchmark(settings: Settings, reps: int, use_llm: bool, versions=VERSIONS, log=print) -> dict:
    server = start_server(port=0)
    base = f"http://127.0.0.1:{server.server_address[1]}"
    trials: list[dict] = []
    flows: list[dict] = []
    try:
        with BrowserManager(headless=True) as bm:
            provider: Any = "auto" if use_llm else None
            for mode in ("no_baseline", "with_baseline"):
                engine = HealingEngine(settings, provider=provider)
                controller = AutomationController(bm, settings, engine, learn_baselines=(mode == "with_baseline"),
                                                  screenshot_dir=None)
                if mode == "with_baseline":
                    learned = controller.run_test(AutomationTest("bench learn@v1", f"{base}/v1", LEARN_STEPS, "bench"))
                    log(f"[{mode}] baseline run on v1: {learned.status}, learned {len(controller.baselines)} element(s)")
                for rep in range(1, reps + 1):
                    for key, v in positive_cases():
                        if v in versions:
                            trials.append(run_positive(controller, bm, base, key, v, rep, mode))
                    for key, v in negative_cases():
                        if v in versions:
                            trials.append(run_negative(controller, bm, base, key, v, rep, mode))
                    for v in versions:
                        flows.append(run_flow(controller, bm, base, v, rep, mode, "healed", [OLD[k] for k in FLOW_KEYS]))
                        flows.append(run_flow(controller, bm, base, v, rep, mode, "truth", [truth_action(k, v) for k in FLOW_KEYS]))
                    log(f"[{mode}] repetition {rep}/{reps} done")
            off = AutomationController(bm, settings, HealingEngine(replace(settings, confidence_threshold=2.0), provider=None),
                                       learn_baselines=False, screenshot_dir=None)
            for v in versions:
                flows.append(run_flow(off, bm, base, v, 1, "no_baseline", "healing_off", [OLD[k] for k in FLOW_KEYS]))
            firefox = bm._browser.version if bm._browser else ""
    finally:
        server.shutdown()
    meta = {
        "date_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"), "reps": reps, "versions": list(versions),
        "python": platform.python_version(), "platform": platform.platform(), "firefox": firefox,
        "playwright": importlib.metadata.version("playwright"), "llm": "enabled" if use_llm and settings.llm_enabled else "none",
        "confidence_threshold": settings.confidence_threshold, "action_timeout_ms": settings.action_timeout_ms,
        "action_retries": settings.action_retries, "max_healing_attempts": settings.max_healing_attempts,
        "positive_cases": len([c for c in positive_cases() if c[1] in versions]),
        "negative_cases": len([c for c in negative_cases() if c[1] in versions]),
    }
    return {"meta": meta, "trials": trials, "flows": flows, "summary": summarize(trials, flows, meta)}


# ---- statistics (pure, unit-tested) -----------------------------------------------------------------------
def _rate(n: int, d: int) -> float | None:
    return n / d if d else None


def _stats(values: list[float]) -> dict | None:
    if not values:
        return None
    ordered = sorted(values)
    p95 = ordered[min(len(ordered) - 1, int(round(0.95 * (len(ordered) - 1))))]
    return {"n": len(values), "mean": statistics.fmean(values), "median": statistics.median(values), "p95": p95}


def summarize(trials: list[dict], flows: list[dict], meta: dict | None = None) -> dict:
    out: dict[str, Any] = {"modes": {}}
    for mode in sorted({t["mode"] for t in trials} | {f["mode"] for f in flows}):
        pos = [t for t in trials if t["mode"] == mode and t["kind"] == "positive"]
        neg = [t for t in trials if t["mode"] == mode and t["kind"] == "negative"]
        count = lambda rows, name: sum(1 for r in rows if r["outcome"] == name)
        correct, wrong = count(pos, "healed_correct"), count(pos, "healed_wrong")
        healed = [t for t in pos if t["outcome"] in ("healed_correct", "healed_wrong")]
        by_case: dict[tuple, set] = {}
        for t in pos + neg:
            by_case.setdefault((t["kind"], t["case"], t["version"]), set()).add(t["outcome"])
        healed_flows = [f for f in flows if f["mode"] == mode and f["label"] == "healed"]
        truth = {(f["version"], f["rep"]): f["duration_ms"] for f in flows
                 if f["mode"] == mode and f["label"] == "truth" and f["status"] == "passed"}
        overhead = [f["duration_ms"] - truth[(f["version"], f["rep"])] for f in healed_flows
                    if f["status"] == "healed" and (f["version"], f["rep"]) in truth]
        out["modes"][mode] = {
            "positive_trials": len(pos), "negative_trials": len(neg),
            "outcomes": {n: count(pos, n) for n in ("healed_correct", "healed_wrong", "refused", "failed")},
            "correct_heal_rate": _rate(correct, len(pos)),
            "heal_attempt_rate": _rate(correct + wrong, len(pos)),
            "false_heal_rate_among_heals": _rate(wrong, len(healed)),
            "refusal_rate_on_positives": _rate(count(pos, "refused"), len(pos)),
            "negative_outcomes": {n: count(neg, n) for n in ("correct_refusal", "false_heal", "failed")},
            "correct_refusal_rate": _rate(count(neg, "correct_refusal"), len(neg)),
            "unstable_cases": sorted(f"{k[1]}@{k[2]}" for k, o in by_case.items() if len(o) > 1),
            "heal_ms": _stats([t["heal_ms"] for t in healed if t["heal_ms"] is not None]),
            "wait_ms": _stats([t["wait_ms"] for t in healed]),
            "recovery_ms": _stats([t["recovery_ms"] for t in healed if t["recovery_ms"] is not None]),
            "flows": len(healed_flows),
            "flows_recovered": sum(1 for f in healed_flows if f["status"] == "healed"),
            "flow_recovery_rate": _rate(sum(1 for f in healed_flows if f["status"] == "healed"), len(healed_flows)),
            "flow_overhead_ms": _stats(overhead),
        }
    off = [f for f in flows if f["label"] == "healing_off"]
    out["control_without_healing"] = {"flows": len(off), "flows_completed": sum(1 for f in off if f["status"] in ("passed", "healed"))}
    return out


def _pct(v: float | None) -> str:
    return "n/a" if v is None else f"{v * 100:.1f}%"


def _ms(s: dict | None, key: str = "mean") -> str:
    return "n/a" if not s else f"{s[key]:.0f} ms"


def render_markdown(result: dict) -> str:
    m, s = result["meta"], result["summary"]
    lines = ["# Benchmark results", "",
             f"Run on {m['date_utc']} UTC | Python {m['python']} | Firefox {m['firefox']} | Playwright {m['playwright']} | "
             f"{m['platform']}", "",
             f"Setup: {m['positive_cases']} broken-locator cases and {m['negative_cases']} must-refuse cases on demo UI "
             f"versions {', '.join(m['versions'])}; {m['reps']} repetition(s); LLM: {m['llm']}; confidence threshold "
             f"{m['confidence_threshold']}; action timeout {m['action_timeout_ms']} ms.", "",
             "Scope: a small demo site. These numbers describe it, not real-world applications.", ""]
    for mode, d in s["modes"].items():
        o, n = d["outcomes"], d["negative_outcomes"]
        lines += [f"## Mode: {mode.replace('_', ' ')}", "", "| Metric | Value |", "|---|---|",
                  f"| Broken-locator trials | {d['positive_trials']} |",
                  f"| Healed onto the correct element | {o['healed_correct']} ({_pct(d['correct_heal_rate'])}) |",
                  f"| Healed onto the wrong element | {o['healed_wrong']} (false heals among heals: {_pct(d['false_heal_rate_among_heals'])}) |",
                  f"| Refused (manual review) | {o['refused']} ({_pct(d['refusal_rate_on_positives'])}) |",
                  f"| Failed otherwise | {o['failed']} |",
                  f"| Must-refuse trials correctly refused | {n['correct_refusal']} of {d['negative_trials']} ({_pct(d['correct_refusal_rate'])}) |",
                  f"| Must-refuse trials wrongly healed | {n['false_heal']} |",
                  f"| Healing time (mean / median / p95) | {_ms(d['heal_ms'])} / {_ms(d['heal_ms'], 'median')} / {_ms(d['heal_ms'], 'p95')} |",
                  f"| Action-timeout wait before healing (mean) | {_ms(d['wait_ms'])} |",
                  f"| Total recovery time per broken step (mean) | {_ms(d['recovery_ms'])} |",
                  f"| Full login flows recovered | {d['flows_recovered']} of {d['flows']} ({_pct(d['flow_recovery_rate'])}) |",
                  f"| Flow overhead vs correct locators (mean) | {_ms(d['flow_overhead_ms'])} |",
                  f"| Cases with differing outcomes across repetitions | {len(d['unstable_cases'])} |", ""]
    c = s["control_without_healing"]
    lines += [f"Control (healing switched off): {c['flows_completed']} of {c['flows']} old-locator flows completed.", ""]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--reps", type=int, default=3, help="repetitions of every trial (default 3)")
    p.add_argument("--quick", action="store_true", help="1 repetition")
    p.add_argument("--timeout-ms", type=int, default=1000, help="action timeout during the benchmark (default 1000)")
    p.add_argument("--llm", action="store_true", help="use the LLM configured in .env (default: deterministic only)")
    p.add_argument("--versions", default=",".join(VERSIONS), help="comma list of UI versions to include (default v2,v3,v4)")
    p.add_argument("--out", default="benchmarks/results", help="folder for the JSON and Markdown results")
    args = p.parse_args(argv)
    settings = replace(load_settings(), action_timeout_ms=args.timeout_ms)
    reps = 1 if args.quick else args.reps
    versions = tuple(v for v in args.versions.split(",") if v in VERSIONS) or VERSIONS
    result = run_benchmark(settings, reps, args.llm, versions)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    (out / f"benchmark_{stamp}.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    md = render_markdown(result)
    (out / f"benchmark_{stamp}.md").write_text(md, encoding="utf-8")
    print("\n" + md)
    print(f"Saved to {out / ('benchmark_' + stamp + '.json')}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
