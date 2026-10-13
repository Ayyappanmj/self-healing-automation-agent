# How to run this project (Windows PowerShell)

Status: all 15 phases complete. The README.md has the project overview; this guide has the step-by-step run instructions. and the Streamlit UI come in later phases.

## 1. Requirements
- Windows 10/11, Python 3.11+ (`python --version`), Git (optional)
- Internet for the one-time install of packages and Chromium

## 2. Setup (once)
```powershell
cd self-healing-automation-agent
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
playwright install chromium
copy .env.example .env
```
If activation is blocked: `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned`, then reopen PowerShell.

## 3. Run the tests
```powershell
pytest -v                                  # everything
pytest -m "not browser"                    # fast, no Chromium needed
pytest --cov --cov-report=term-missing --cov-report=html   # measure coverage (open htmlcov\index.html)
python -m scripts.mutation_check           # break the code on purpose; tests must notice
```
See `docs/testing.md` for what is tested where.
Tests marked "real browser" launch headless Chromium. If Chromium isn't installed they are skipped, not failed.

## 4. Try each phase
| Phase | Command | What you see |
|---|---|---|
| 2 Demo site | `python -m demo_site.app` then open http://127.0.0.1:8000 | Login page in 4 UI versions (/v1 to /v4) |
| 3 Automation | `python -m scripts.phase3_demo` | Chromium opens; v1 steps PASS on v1, FAIL on v2 |
| 4 Failure detection | `python -m scripts.phase4_demo` | Each failure classified (not found, hidden, ambiguous, invalid); screenshots saved to `screenshots/` |
| 5 DOM inspection | `python -m scripts.phase5_demo` | After the v1 locator fails on v2/v3/v4, prints the page's elements (id, testid, role, text, label, path, visibility); saves `data/snapshot_v*.json` |
| 6 Candidate locators | `python -m scripts.phase6_demo` | For v2/v3/v4, lists the candidate locators generated for buttons and how many elements each really matches in the browser |
| 7 Scoring | `python -m scripts.phase7_demo` | For v2/v3/v4, six broken steps are scored; each shows confidence and HEAL or MANUAL_REVIEW (the missing `#checkout` is refused) |
| 8 LLM reasoner | `python -m scripts.phase8_demo` | Borderline cases are sent to the LLM if enabled; otherwise shows the prompt that would be sent. No browser needed |
| 9 Full healing loop | `python -m scripts.phase9_demo` | Real browser: test passes on v1, then the same old locators are healed on v2, v3, v4; a missing target is refused. Prints each healing step |
| 10 Dashboard | `streamlit run app.py` | The app at http://localhost:8501. Click **Run Demo**; a browser window opens and you watch locators fail and heal |
| 10 CLI runner | `python -m agent.runner --versions v2,v3` | Same run without the UI; prints one JSON event per line |

Run all commands from the project root, always with `python -m ...` for the scripts.

## 5. Optional: free local LLM with Ollama
The project works without any LLM. To try the AI reasoner (unverified on my side, follow Ollama's own docs if a step differs):
1. Install Ollama from https://ollama.com/download
2. `ollama pull llama3.2`
3. In `.env` set `LLM_PROVIDER=ollama` (defaults: `OLLAMA_URL=http://localhost:11434`, `OLLAMA_MODEL=llama3.2`)
4. `python -m scripts.phase8_demo`
Optional live test: `$env:RUN_LIVE_LLM_TEST="1"; pytest tests/test_llm_reasoner.py -k live -v`
For Google Gemini set `LLM_PROVIDER=gemini` and `GEMINI_API_KEY` in `.env` (never commit it).

## 6. Settings
Edit `.env` (copy of `.env.example`). Useful now:
- `HEADLESS=true` runs browsers without a window
- `ACTION_TIMEOUT_MS=3000` how long an action waits before failing
- `CONFIDENCE_THRESHOLD=0.75` below this, healing is refused (manual review)
- `MAX_HEALING_ATTEMPTS=2` locators validated per failure, `ACTION_RETRIES=2` retries of the healed action
`LLM_PROVIDER`, `LLM_TIMEOUT_S` control the AI reasoner. `.env` is git-ignored: never commit keys.

## 7. Troubleshooting
| Problem | Fix |
|---|---|
| `ModuleNotFoundError: agent` | Run from the project root, use `python -m scripts.<name>` |
| `Executable doesn't exist` | Run `playwright install chromium` |
| Port 8000 in use | Close the other server, or change the port in `demo_site/app.py` |
| Activation blocked | See the ExecutionPolicy command above |
| Pytest skips browser tests | Chromium missing, or the venv isn't active |
| Run Demo shows "Executable doesn't exist" | `playwright install chromium` |
| Run Demo does nothing / error box | Open the "Runner console output" expander; try `python -m agent.runner` in PowerShell to see the raw error |
| Dashboard shows zeros | No run is stored yet, or `DB_PATH` in `.env` changed. Settings > Stored data shows the database path and row counts |
| "Could not save to storage" in the run log | The run still completes. Check that `data/` is writable and no other program has `healing.db` locked |

## 8. Using the dashboard
1. `streamlit run app.py`, then click **Run Demo** on the Dashboard tab (allow ~30-60 s; the browser window is visible).
2. Watch the live log: a step fails, the DOM is inspected, a replacement is chosen, validated and retried.
3. **Self-Healing** tab: pick an event for the full explanation, candidate scores, AI prompt/reply and validation.
4. **DOM Inspector**: see what the agent sees on any page and which locators it would generate.
5. **Healing History / Test Reports**: filter and download JSON, CSV and Markdown.
6. **Settings**: threshold, retries and the optional LLM. Changes apply to runs started from the app.
Browser automation runs in a subprocess (`agent.runner`) so Streamlit and Playwright do not conflict. Every run is saved by the runner to a local SQLite database (`data/healing.db`), so the dashboard, history and reports survive a refresh and a restart. Metrics are computed only from stored runs.

## 9. Storage and logs (Phase 11)
| What | Where | Notes |
|---|---|---|
| Runs, test results, healing events, repaired locators | `data/healing.db` (SQLite) | Written by `agent.runner`, read by the dashboard. Open it with any SQLite viewer |
| Structured healing log | `data/logs/healing.jsonl` | One JSON object per healing event with every field from the logging spec |
| Readable log | `data/logs/agent.log` | Rotates at 1 MB, keeps 3 old files |
Dashboard: **Healing History** has three sub-tabs (events, locator repository, logs) with downloads; **Settings > Stored data** shows counts, exports everything as JSON, and can delete all stored runs.
`REUSE_HEALED_LOCATORS=true` (or `--reuse-healed`) makes later runs use locators repaired earlier instead of healing again. It is off by default so the demo shows healing every time.
`python -m agent.runner --no-persist` runs without saving anything. All of these paths are git-ignored.

## 10. Recording the demo (Phase 14)
```powershell
python -m scripts.demo_preflight           # is everything ready for an honest recording?
python -m scripts.demo_preflight --reset   # optional clean slate (asks before deleting)
.\scripts\make_gif.ps1 -In recordings\demo.mp4 -Out docs\demo.gif -Start 8 -Duration 25
```
The full script, shot list, honesty rules and README steps are in `docs/demo-recording.md`.

## 11. Benchmarks, deployment, GitHub (Phase 15)
```powershell
python -m scripts.benchmark --quick        # real measurements on the demo site (see docs/benchmarks.md)
python -m scripts.resume_bullets           # resume bullets from measured numbers only
python -m scripts.prepush_check            # secrets, placeholders, large files before git push
```
Deployment options: `docs/deployment.md`. GitHub steps, repository description, topics and commit plan: `docs/github-setup.md`.

## 12. Project map
```
agent/     runner.py, scenarios.py, logging_setup.py, config.py, failure_detector.py, dom_inspector.py, locator_generator.py, candidate_scorer.py, llm_reasoner.py, locator_validator.py, healing_engine.py, controller.py
browser/   manager.py, actions.py
demo_site/ app.py, versions/ (v1-v4 HTML)
storage/   database.py (SQLite)
app.py + ui/  Streamlit pages (dashboard, demo, healing, inspector, history, reports, settings)
scripts/   phase3_demo.py, phase4_demo.py, phase5_demo.py, phase6_demo.py, phase7_demo.py, phase8_demo.py, phase9_demo.py
tests/     one file per module + test_integration.py (real browser end-to-end) + fakes.py (browser-free doubles)
docs/      architecture.md (Mermaid diagram)
```
