# Benchmarks and resume metrics

Do not quote a number about this project that you did not measure. This tool measures the numbers; the resume tool turns
them into bullets and refuses to invent anything.

```powershell
python -m scripts.benchmark                  # about 5 minutes: 3 repetitions, deterministic mode (no LLM)
python -m scripts.benchmark --quick          # 1 repetition, about 1.5 minutes
python -m scripts.benchmark --llm            # also use the LLM configured in .env, to report it separately
python -m scripts.resume_bullets             # bullets from the newest result in benchmarks\results
```

Results are saved as `benchmarks\results\benchmark_<timestamp>.json` and `.md`, together with the environment (Python,
Firefox, Playwright, OS), the settings used and the number of cases.

## What is measured

| Metric | Definition |
|---|---|
| Correct heal rate | Broken-locator trials where the agent healed **and** the new locator points at the same DOM node as the ground-truth locator, divided by all broken-locator trials |
| False heals | Trials healed onto a different element. Reported separately; this is the number to watch |
| Refusal rate | Trials the agent declined (manual review). Not counted as success, not counted as a false heal |
| Must-refuse trials | Targets that do not exist (`button#checkout`) or were removed (the remember-me box on UI v4). The agent must refuse; a heal here is a serious error |
| Healing time | From the start of healing to the end of the retry. Mean, median and p95 over healed trials |
| Action-timeout wait | Time the failed action waited before healing started (set by `ACTION_TIMEOUT_MS`, 1000 ms in the benchmark) |
| Total recovery time | Wait plus healing time, per broken step |
| Failed-test recovery rate | Full login flows (old locators on a changed UI) that completed after healing. A control run with healing switched off shows how many would have failed anyway |
| Execution overhead | Duration of a recovered flow minus the same flow written with correct locators, for the same UI version and repetition |
| Unstable cases | Cases whose outcome differed between repetitions (expected: 0) |

Two modes are reported side by side: **without baseline** (the agent only has the broken locator and the step name) and
**with baseline** (it first learned the original elements from a passing run). Say which one you quote.

## How to read the results honestly

- The site is small: three changed UI versions of one login page. The numbers describe it, not the web.
- Ground truth is hand-written in `scripts/benchmark.py` (`TRUTH`). Check it if you change the demo pages; a test verifies
  that every ground-truth locator matches exactly one element.
- Healing time is tens of milliseconds because the demo pages are tiny. The dominant cost of recovering is the action
  timeout that elapses before healing starts.
- One refused step stops a whole flow, so flow recovery is a strict, low number. The per-locator numbers say more.
- Rates on 17 cases move in steps of about 6 percentage points. Quote the counts ("15 of 17"), not just percentages.

## Resume bullets

`python -m scripts.resume_bullets` prints bullets in the form "Accomplished X, as measured by Y, by doing Z", filled only
with numbers from the result file, each stating its scope. It also prints reminders (sample size, demo-site scope,
which mode). Edit the wording to your voice, but keep the counts, the scope and the "0 wrong heals" style statements
exactly as measured. If a metric is missing from the file, that bullet is skipped. Without a result file it prints only the
evidence-based bullet:

> Built a self-healing Playwright automation agent that detects broken UI locators, analyzes DOM candidates, validates
> replacement selectors, and retries failed browser actions.

Do not add claims such as "reduced maintenance time by N%". Nothing here measures maintenance time.
