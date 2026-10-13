# Testing

```powershell
pytest                                   # everything (browser tests skip themselves if Firefox is missing)
pytest -m "not browser"                  # fast: no Firefox needed
pytest -m browser                        # only the real-browser tests
pytest --cov --cov-report=term-missing --cov-report=html   # measure coverage; open htmlcov\index.html
python -m scripts.mutation_check         # break the code on purpose; the tests must notice
```

Coverage is whatever `pytest --cov` reports on your machine. This repository does not publish a coverage
percentage, because a number is only worth quoting when you measured it yourself.
The UI page modules (`ui/dashboard.py`, ...) are exercised by `tests/test_ui_smoke.py`, which needs Streamlit installed.

## What is tested where

| Area (from the project spec) | Tests |
|---|---|
| Locator execution | `test_actions.py`, `test_error_handling.py` (assert_visible, odd inputs) |
| Broken locator detection | `test_failure_detector.py`, `test_error_handling.py` |
| Candidate generation | `test_dom_inspector.py`, `test_locator_generator.py` |
| Locator scoring | `test_candidate_scorer.py` |
| Locator validation | `test_validator.py` |
| Healing engine | `test_healing.py`, `test_controller.py`, `test_integration.py` (real browser) |
| Low-confidence handling | `test_healing.py`, `test_candidate_scorer.py`, `test_controller.py`, `test_llm_reasoner.py`, `test_error_handling.py` (every confidence cap) |
| Retry mechanism | `test_healing.py` (action retries, validation fallbacks), `test_llm_reasoner.py` (JSON retry), `test_controller.py` (crash restart) |
| JSON parsing | `test_llm_reasoner.py`, `test_error_handling.py`, `test_storage.py` (JSON Lines) |
| Error handling | `test_error_handling.py`, `test_browser_manager.py`, `test_runner_cli.py`, `test_storage.py`, `test_llm_reasoner.py` |
| Everything else | storage (`test_storage.py`), UI logic (`test_ui_logic.py`), demo site (`test_demo_site.py`), config (`test_smoke.py`) |

## How the tests are built

- `tests/fakes.py` builds a browser-free page from the real demo HTML. It only resolves the locator strings the generator
  can produce, so a broken locator really matches nothing. This lets the healing engine, controller and runner be tested
  end to end in milliseconds.
- Tests marked `browser` (automatic, see `tests/conftest.py`) run the same flows in a real Firefox. They are the final check
  that the generated locator syntax (`role=`, `:has-text`, xpath) is accepted by Playwright.
- The LLM is never mocked in the demo; tests use a scripted fake provider and an injected fake HTTP layer. An optional live
  test runs only with `RUN_LIVE_LLM_TEST=1` and a configured provider.
- `scripts/mutation_check.py` injects 12 deliberate bugs into safety rules (confidence gate, validation, caps, privacy, retry
  limits) and requires the browser-free tests to fail for each.
