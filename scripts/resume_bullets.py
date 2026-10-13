"""Turn a benchmark result into resume bullets. Only measured numbers are used; nothing is invented.

    python -m scripts.resume_bullets                       # newest file in benchmarks/results
    python -m scripts.resume_bullets path\to\benchmark.json

Format: "Accomplished [X] as measured by [Y] by doing [Z]". Each bullet states its scope (a demo site), because a number
without its scope overclaims. Bullets whose inputs are missing are skipped.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

NO_NUMBERS = ("Built a self-healing Playwright automation agent that detects broken UI locators, analyzes DOM candidates, "
              "validates replacement selectors, and retries failed browser actions.")
SMALL_SAMPLE = 10


def latest_result(folder: str | Path = "benchmarks/results") -> Path | None:
    files = sorted(Path(folder).glob("benchmark_*.json"))
    return files[-1] if files else None


def _pct(v: float) -> str:
    return f"{v * 100:.0f}%"


def build_bullets(result: dict, mode: str = "with_baseline") -> list[str]:
    meta, summary = result["meta"], result["summary"]
    d = summary["modes"].get(mode)
    if not d:
        return [NO_NUMBERS]
    n_versions = len(meta["versions"])
    scope = f"on a {n_versions}-variant demo web app"
    bullets = [NO_NUMBERS]
    o, trials = d["outcomes"], d["positive_trials"]
    if trials:
        wrong = o["healed_wrong"]
        bullets.append(
            f"Automatically recovered {o['healed_correct']} of {trials} intentionally broken UI locators ({_pct(d['correct_heal_rate'])}) "
            f"{scope}, with {wrong} healed onto the wrong element, as verified against ground-truth DOM nodes, "
            "by scoring DOM candidates and validating each replacement in the live page before retrying.")
    if d["negative_trials"]:
        n = d["negative_outcomes"]
        bullets.append(
            f"Refused to act on {n['correct_refusal']} of {d['negative_trials']} locators whose target was missing or removed "
            f"({_pct(d['correct_refusal_rate'])}), with {n['false_heal']} wrong heals, "
            "by gating every repair on a confidence threshold and validation checks.")
    if d["heal_ms"]:
        h, w = d["heal_ms"], d["wait_ms"]
        wait = f", after a {meta['action_timeout_ms']} ms action timeout" if w else ""
        bullets.append(
            f"Repaired a broken locator in {h['mean']:.0f} ms on average (median {h['median']:.0f} ms, p95 {h['p95']:.0f} ms) "
            f"{scope}{wait}, by inspecting the DOM, generating and scoring candidates, validating and retrying the action.")
    if d["flows"]:
        extra = ""
        if d["flow_overhead_ms"]:
            extra = (f", taking {d['flow_overhead_ms']['mean'] / 1000:.1f} s longer on average than the same flow with correct "
                     f"locators (dominated by the {meta['action_timeout_ms']} ms action timeout on each broken step)")
        control = summary.get("control_without_healing", {})
        base = f" (all {control['flows']} failed without healing)" if control.get("flows") and not control.get("flows_completed") else ""
        bullets.append(
            f"Completed {d['flows_recovered']} of {d['flows']} login test flows that broke after UI changes{base}{extra}, "
            f"{scope}, using the self-healing agent.")
    return bullets


def caveats(result: dict) -> list[str]:
    meta = result["meta"]
    notes = [f"Measured {meta['date_utc']} UTC on {meta['platform']}, {meta['positive_cases']} broken-locator cases, "
             f"{meta['reps']} repetition(s), LLM: {meta['llm']}."]
    if meta["positive_cases"] < SMALL_SAMPLE:
        notes.append("Small sample: say how many cases it was.")
    notes.append("Results describe the bundled demo site only. Do not describe them as real-world performance.")
    notes.append("The flow bullet is strict: one refused step stops the whole flow. The per-locator bullets say more about the agent.")
    notes.append("Be ready to explain the 'with baseline' mode: the agent learned the original elements from a passing run first.")
    return notes


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("path", nargs="?", help="benchmark JSON (default: newest in benchmarks/results)")
    p.add_argument("--mode", choices=["no_baseline", "with_baseline"], default="with_baseline")
    args = p.parse_args(argv)
    path = Path(args.path) if args.path else latest_result()
    if not path or not path.exists():
        print("No benchmark result found. Run: python -m scripts.benchmark")
        print("\nUntil then, use only this evidence-based bullet:\n- " + NO_NUMBERS)
        return 1
    result = json.loads(path.read_text(encoding="utf-8"))
    print(f"Using {path}\n")
    for b in build_bullets(result, args.mode):
        print("- " + b)
    print("\nBefore you use these:")
    for note in caveats(result):
        print("  * " + note)
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
