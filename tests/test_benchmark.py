import json

import pytest

from scripts import benchmark as bm
from scripts import resume_bullets as rb


def trial(kind, outcome, mode="with_baseline", case="login", version="v2", **kw):
    base = {"kind": kind, "mode": mode, "case": case, "version": version, "rep": 1, "outcome": outcome,
            "heal_ms": None, "wait_ms": 0.0, "recovery_ms": None, "confidence": 0.9}
    return {**base, **kw}


def healed(outcome="healed_correct", heal=50.0, wait=1000.0, **kw):
    return trial("positive", outcome, heal_ms=heal, wait_ms=wait, recovery_ms=heal + wait, **kw)


def flow(label, status, ms, version="v2", rep=1, mode="with_baseline"):
    return {"kind": "flow", "label": label, "status": status, "duration_ms": ms, "version": version, "rep": rep,
            "mode": mode, "healed_steps": 1}


# --- ground truth definitions ----------------------------------------------------------------------------
def test_case_inventory():
    assert len(bm.positive_cases()) == 17 and len(bm.negative_cases()) == 4
    assert ("remember", "v4") in bm.negative_cases() and ("remember", "v4") not in bm.positive_cases()
    assert set(bm.TRUTH) == set(bm.VERSIONS) and all(set(v) == set(bm.CASE_KEYS) for v in bm.TRUTH.values())


def test_truth_action_keeps_the_action_but_swaps_the_locator():
    a = bm.truth_action("login", "v2")
    assert a.type == "click" and a.locator == '[data-testid="login-button"]' and a.name == "Click login"
    assert bm.OLD["login"].locator == "button#login"                    # the old action is untouched


# --- summary arithmetic -----------------------------------------------------------------------------------
def test_summary_counts_and_rates_are_exact():
    trials = [healed(), healed(), healed("healed_wrong"), trial("positive", "refused"), trial("positive", "failed"),
              trial("negative", "correct_refusal"), trial("negative", "correct_refusal"), trial("negative", "false_heal")]
    d = bm.summarize(trials, [])["modes"]["with_baseline"]
    assert d["positive_trials"] == 5 and d["negative_trials"] == 3
    assert d["outcomes"] == {"healed_correct": 2, "healed_wrong": 1, "refused": 1, "failed": 1}
    assert d["correct_heal_rate"] == 0.4 and d["heal_attempt_rate"] == 0.6
    assert d["false_heal_rate_among_heals"] == pytest.approx(1 / 3) and d["refusal_rate_on_positives"] == 0.2
    assert d["negative_outcomes"] == {"correct_refusal": 2, "false_heal": 1, "failed": 0}
    assert d["correct_refusal_rate"] == pytest.approx(2 / 3)


def test_timing_stats_use_only_healed_trials():
    trials = [healed(heal=h) for h in (10.0, 20.0, 30.0, 40.0)] + [trial("positive", "refused", heal_ms=999.0)]
    s = bm.summarize(trials, [])["modes"]["with_baseline"]
    assert s["heal_ms"]["n"] == 4 and s["heal_ms"]["mean"] == 25.0 and s["heal_ms"]["median"] == 25.0
    assert s["heal_ms"]["p95"] == 40.0 and s["recovery_ms"]["mean"] == 1025.0


def test_empty_input_gives_none_not_zero_division():
    d = bm.summarize([trial("positive", "refused")], [])["modes"]["with_baseline"]
    assert d["correct_heal_rate"] == 0.0 and d["false_heal_rate_among_heals"] is None
    assert d["heal_ms"] is None and d["flow_recovery_rate"] is None and d["flow_overhead_ms"] is None
    assert bm.summarize([], [])["modes"] == {}


def test_flow_recovery_and_overhead_compare_against_the_truth_flow():
    flows = [flow("healed", "healed", 6000.0, "v2"), flow("truth", "passed", 900.0, "v2"),
             flow("healed", "manual_review", 4000.0, "v3"), flow("truth", "passed", 800.0, "v3"),
             flow("healing_off", "manual_review", 1000.0, "v2", mode="no_baseline")]
    s = bm.summarize([healed()], flows)
    d = s["modes"]["with_baseline"]
    assert d["flows"] == 2 and d["flows_recovered"] == 1 and d["flow_recovery_rate"] == 0.5
    assert d["flow_overhead_ms"]["mean"] == 5100.0                      # only the recovered flow counts
    assert s["control_without_healing"] == {"flows": 1, "flows_completed": 0}


def test_unstable_cases_are_reported():
    trials = [healed(case="a"), healed("healed_wrong", case="a"), healed(case="b"), healed(case="b")]
    assert bm.summarize(trials, [])["modes"]["with_baseline"]["unstable_cases"] == ["a@v2"]


def test_modes_are_kept_apart():
    trials = [healed(mode="no_baseline"), trial("positive", "refused", mode="with_baseline")]
    modes = bm.summarize(trials, [])["modes"]
    assert modes["no_baseline"]["correct_heal_rate"] == 1.0 and modes["with_baseline"]["correct_heal_rate"] == 0.0


def result_for(trials, flows=None, **meta):
    m = {"date_utc": "2026-10-04T00:00:00+00:00", "python": "3.12", "platform": "test", "firefox": "1", "playwright": "1",
         "reps": 1, "versions": ["v2", "v3", "v4"], "llm": "none", "confidence_threshold": 0.75, "action_timeout_ms": 1000,
         "positive_cases": 17, "negative_cases": 4, **meta}
    return {"meta": m, "trials": trials, "flows": flows or [], "summary": bm.summarize(trials, flows or [], m)}


def test_markdown_report_contains_the_measured_numbers_and_the_scope_warning():
    r = result_for([healed(), healed(), trial("positive", "refused"), trial("negative", "correct_refusal")])
    md = bm.render_markdown(r)
    assert "66.7%" in md and "2 (66.7%)" in md and "1 of 1 (100.0%)" in md
    assert "describe it, not real-world applications" in md and "action timeout 1000 ms" in md


# --- resume bullets ------------------------------------------------------------------------------------------
def test_bullets_use_only_measured_numbers():
    r = result_for([healed(), healed(), healed("healed_wrong"), trial("positive", "refused"),
                    trial("negative", "correct_refusal"), trial("negative", "correct_refusal")],
                   [flow("healed", "healed", 6000.0), flow("truth", "passed", 900.0),
                    flow("healing_off", "manual_review", 900.0, mode="no_baseline")])
    text = "\n".join(rb.build_bullets(r))
    assert "2 of 4 intentionally broken" in text and "(50%)" in text and "1 healed onto the wrong element" in text
    assert "2 of 2 locators whose target was missing" in text and "(100%)" in text
    assert "50 ms on average" in text and "5.1 s longer" in text and "all 1 failed without healing" in text
    assert text.count("3-variant demo web app") >= 3


def test_bullets_never_overclaim_beyond_the_scope():
    r = result_for([healed()])
    for bullet in rb.build_bullets(r)[1:]:
        assert "demo web app" in bullet
    assert rb.build_bullets(r)[0] == rb.NO_NUMBERS and "%" not in rb.NO_NUMBERS


def test_no_trials_means_only_the_evidence_based_bullet():
    assert rb.build_bullets(result_for([])) == [rb.NO_NUMBERS]
    assert rb.build_bullets(result_for([healed()]), mode="no_baseline") == [rb.NO_NUMBERS]


def test_small_sample_caveat_and_scope_note():
    notes = " ".join(rb.caveats(result_for([healed()], positive_cases=4)))
    assert "Small sample" in notes and "demo site only" in notes
    assert "Small sample" not in " ".join(rb.caveats(result_for([healed()])))


def test_latest_result_and_cli(tmp_path, capsys=None):
    import contextlib
    import io
    assert rb.latest_result(tmp_path) is None
    (tmp_path / "benchmark_20260101_000000.json").write_text(json.dumps(result_for([healed()])), encoding="utf-8")
    (tmp_path / "benchmark_20260202_000000.json").write_text(json.dumps(result_for([healed(), healed()])), encoding="utf-8")
    assert rb.latest_result(tmp_path).name == "benchmark_20260202_000000.json"
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        code = rb.main([str(rb.latest_result(tmp_path))])
    assert code == 0 and "2 of 2 intentionally broken" in out.getvalue() and "Before you use these" in out.getvalue()
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        assert rb.main([str(tmp_path / "missing.json")]) == 1
    assert rb.NO_NUMBERS in out.getvalue()


# --- real browser ----------------------------------------------------------------------------------------------
@pytest.mark.parametrize("version", ["v2", "v3", "v4"])
def test_every_ground_truth_locator_matches_exactly_one_element(browser_page, version):
    page, base = browser_page
    page.goto(f"{base}/{version}")
    for key, locator in bm.TRUTH[version].items():
        count = page.locator(locator).count() if locator else 0
        assert count == (1 if locator else 0), (version, key, locator, count)


def test_removed_element_really_is_gone_and_old_locators_really_are_broken(browser_page):
    page, base = browser_page
    page.goto(f"{base}/v4")
    assert page.locator("input[type=checkbox]").count() == 0 and page.locator("text=Keep me").count() == 0
    for version in bm.VERSIONS:
        page.goto(f"{base}/{version}")
        assert all(page.locator(a.locator).count() == 0 for a in bm.OLD.values())


def test_same_dom_node_distinguishes_identity_from_lookalikes(browser_page):
    page, base = browser_page
    page.goto(f"{base}/v2")
    assert bm.same_dom_node(page, '[data-testid="login-button"]', "main > form > button")
    assert not bm.same_dom_node(page, '[data-testid="login-button"]', '[data-testid="forgot-link"]')
    assert not bm.same_dom_node(page, '[data-testid="login-button"]', "#does-not-exist")


@pytest.mark.browser
def test_tiny_real_benchmark_run_produces_a_consistent_result(tmp_path):
    """Runs the CLI in a separate process (Playwright's sync API allows one instance per process)."""
    import subprocess
    import sys
    from pathlib import Path
    root = Path(__file__).resolve().parent.parent
    out = subprocess.run([sys.executable, "-m", "scripts.benchmark", "--quick", "--versions", "v2", "--timeout-ms", "600",
                          "--out", str(tmp_path)], capture_output=True, text=True, cwd=root, timeout=600)
    if "Executable doesn't exist" in out.stderr + out.stdout:
        pytest.skip("Firefox is not installed")
    assert out.returncode == 0, out.stderr[-500:]
    (path,) = tmp_path.glob("benchmark_*.json")
    result = json.loads(path.read_text(encoding="utf-8"))
    d = result["summary"]["modes"]["with_baseline"]
    assert result["meta"]["positive_cases"] == 6 and d["positive_trials"] == 6
    assert sum(d["outcomes"].values()) == 6 and d["negative_trials"] == 1
    assert d["outcomes"]["healed_wrong"] == 0 and d["negative_outcomes"]["false_heal"] == 0
    assert result["summary"]["control_without_healing"]["flows_completed"] == 0
    assert (tmp_path / path.name.replace(".json", ".md")).exists()
