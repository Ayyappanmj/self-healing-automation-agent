# Changelog

## 0.1.0

First public version.

- Failure detection that separates healable locator problems from hidden elements, invalid selectors and browser crashes.
- DOM inspector, candidate generation (11 strategies), transparent scoring with safety caps.
- Optional LLM reasoning that must choose from the candidates.
- Live-page validation, bounded retries, confidence gate, browser-crash recovery.
- Streamlit dashboard, SQLite history, structured logs, CSV/JSON/Markdown exports, read-only showcase mode.
- Demo site with four UI versions; benchmark with ground truth; resume-bullet generator; mutation check.
- Locators built from text that looks dynamic (typed values, numbers, emails, prices) are never generated.

See the Limitations section of the README for what is not covered.
