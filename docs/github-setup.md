# GitHub setup

## 1. Before the first push

```powershell
python -m scripts.prepush_check      # secrets, placeholders, large files, files that must not be committed
pytest -m "not browser"              # fast check
pytest                               # full check (needs Chromium)
```

`prepush_check` will flag two placeholders you must fill in yourself:
- `LICENSE`: replace `[Your Name]` with your name.
- `README.md`: replace `<your-username>` in the clone command with your GitHub username.

It also looks for API-key patterns. `.env` is git-ignored; keep real keys only there. If a key is ever committed,
**revoke it at the provider immediately**: deleting it from a later commit does not remove it from history.

## 2. Create the repository

Name: `self-healing-automation-agent`. Public. Do not let GitHub add a README or licence (you already have them).

## Repository description

Paste into the "About" box (the limit is 350 characters):

```text
Playwright test automation that repairs its own broken locators: detects a failure, inspects the DOM, scores replacement candidates (optional local LLM via Ollama), validates the fix in the live page, retries, and refuses when unsure. Streamlit dashboard, SQLite history.
```

## Topics

```text
playwright, test-automation, self-healing-tests, web-automation, qa-automation, streamlit, python, llm, ollama, sqlite, dom-inspection, portfolio-project
```

Add these under About > Topics. Put the hosted showcase URL (if you deploy one, see `deployment.md`) in the "Website" field.

## 3. Commit structure

Small commits grouped by feature, in the order the project was built. Use
[Conventional Commits](https://www.conventionalcommits.org) messages.

```powershell
git init
git branch -M main

git add .gitignore LICENSE requirements.txt .env.example pytest.ini .coveragerc conftest.py agent/__init__.py agent/config.py browser/__init__.py storage/__init__.py ui/__init__.py scripts/__init__.py tests/__init__.py tests/test_smoke.py tests/test_config.py data/.gitkeep screenshots/.gitkeep benchmarks/results/.gitkeep showcase/.gitkeep
git commit -m "chore: project scaffold, configuration and test setup"

git add demo_site tests/test_demo_site.py
git commit -m "feat(demo-site): local login page in four UI versions that break locators differently"

git add browser tests/test_actions.py tests/test_browser_manager.py scripts/phase3_demo.py
git commit -m "feat(browser): Playwright manager with crash recovery and a locator executor"

git add agent/failure_detector.py agent/dom_inspector.py tests/test_failure_detector.py tests/test_dom_inspector.py scripts/phase4_demo.py scripts/phase5_demo.py
git commit -m "feat(agent): classify failures and inspect the live DOM"

git add agent/locator_generator.py agent/candidate_scorer.py tests/test_locator_generator.py tests/test_candidate_scorer.py scripts/phase6_demo.py scripts/phase7_demo.py
git commit -m "feat(agent): candidate locator generation and transparent scoring"

git add agent/llm_reasoner.py tests/test_llm_reasoner.py scripts/phase8_demo.py
git commit -m "feat(agent): optional LLM reasoner with strict JSON, fallbacks and safety caps"

git add agent/locator_validator.py agent/healing_engine.py agent/controller.py tests/fakes.py tests/conftest.py tests/test_validator.py tests/test_healing.py tests/test_controller.py tests/test_error_handling.py tests/test_integration.py scripts/phase9_demo.py
git commit -m "feat(agent): healing engine with validation, retry and confidence gate"

git add storage agent/logging_setup.py tests/test_storage.py tests/test_showcase.py
git commit -m "feat(storage): SQLite history, structured logs and showcase export"

git add agent/runner.py agent/scenarios.py app.py ui .streamlit tests/test_runner_cli.py tests/test_ui_logic.py tests/test_ui_smoke.py
git commit -m "feat(ui): Streamlit dashboard driven by a subprocess runner"

git add README.md RUN_GUIDE.md CHANGELOG.md docs showcase/README.md tests/test_docs.py
git commit -m "docs: README, architecture, testing, deployment and recording guides"

git add scripts/benchmark.py scripts/resume_bullets.py scripts/mutation_check.py scripts/demo_preflight.py scripts/make_gif.ps1 scripts/prepush_check.py tests/test_benchmark.py tests/test_demo_preflight.py tests/test_prepush_check.py
git commit -m "feat(tools): benchmark with ground truth, resume bullets, mutation check, pre-flight and pre-push checks"

git add .github Dockerfile .dockerignore
git commit -m "ci: run the test suite on GitHub Actions; add a headless Docker image"
```

Intermediate commits will not pass the whole suite (later tests depend on earlier-built modules and shared fixtures), and
that is normal for a feature-by-feature history. If you want every commit green, make one commit instead.

## 4. Push and tag

```powershell
git remote add origin https://github.com/<your-username>/self-healing-automation-agent.git
git push -u origin main
git tag -a v0.1.0 -m "First public version"
git push origin v0.1.0
```

## 5. After the first push

1. Open the **Actions** tab and check that the `tests` workflow is green (it installs Chromium and runs the full suite,
   including the Streamlit smoke test). Fix anything red before sharing the link.
2. Once it is green, add the badge to the top of `README.md` (replace `<your-username>`):
   ```markdown
   [![tests](https://github.com/<your-username>/self-healing-automation-agent/actions/workflows/tests.yml/badge.svg)](https://github.com/<your-username>/self-healing-automation-agent/actions/workflows/tests.yml)
   ```
   Do not add it earlier: a badge for a workflow that has not run is misleading.
3. Record the demo (`demo-recording.md`) and add `docs/demo.gif`.
4. Run the benchmark on your machine (`benchmarks.md`) and add a short, dated "Results" note to the README only if you
   keep the scope statement next to the numbers.
5. Pin the repository on your profile.
