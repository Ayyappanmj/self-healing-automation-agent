"""Sanity check for the test suite itself: break the code on purpose and confirm the tests notice.

Run from the project root:  python -m scripts.mutation_check
Each "mutant" is one small deliberate bug in a safety-critical rule. The browser-free tests
(`pytest -m "not browser"`) must fail for every one. Source files are restored afterwards.
"""
import pathlib
import subprocess
import sys

MUTANTS = [
    ("confidence gate ignored", "agent/healing_engine.py",
     "if not proposal.should_heal(s.confidence_threshold):", "if False:"),
    ("identity check removed", "agent/locator_validator.py",
     'res.checks["intended_element"] = same_element(res.identity, expected) if expected is not None else None',
     'res.checks["intended_element"] = None'),
    ("ambiguity cap removed (scorer)", "agent/candidate_scorer.py",
     "if not best.candidate.unique:\n        confidence = min(confidence, AMBIGUOUS_CAP)",
     "if False:\n        confidence = min(confidence, AMBIGUOUS_CAP)"),
    ("LLM may invent locators", "agent/llm_reasoner.py",
     '    if chosen is None:\n        return LLMDecision(True, locator=loc, reason=reason, rejection="locator is not in the candidate list")',
     "    if chosen is None:\n        return LLMDecision(True, None, loc, 'x', 0.99, reason, True)"),
    ("hidden elements become healable", "agent/failure_detector.py",
     "HEALABLE = {FailureType.NOT_FOUND, FailureType.AMBIGUOUS}",
     "HEALABLE = {FailureType.NOT_FOUND, FailureType.AMBIGUOUS, FailureType.NOT_INTERACTABLE}"),
    ("action retries ignored", "agent/healing_engine.py", "attempts = max(1, s.action_retries)", "attempts = 1"),
    ("LLM boost uncapped", "agent/llm_reasoner.py",
     "conf = min(AGREE_CAP, result.confidence + AGREE_MAX_BOOST, max(result.confidence, blend))",
     "conf = min(AGREE_CAP, max(result.confidence, d.confidence))"),
    ("input values leak into snapshots", "agent/dom_inspector.py",
     'text = a.get("value", "") if is_button_input else node.text',
     'text = a.get("value", "") or node.text'),
    ("max_healing_attempts ignored", "agent/healing_engine.py",
     "alternatives[: max(1, s.max_healing_attempts)]", "alternatives"),
    ("storage errors crash the run", "agent/runner.py",
     '        except Exception as exc:\n            msg = f"Could not save',
     '        except ZeroDivisionError as exc:\n            msg = f"Could not save'),
    ("baseline can lower confidence", "agent/candidate_scorer.py",
     "base += PROXIMITY_BONUS * prox * (1 - base)", "base = base * prox"),
    ("healed locators not reused", "agent/controller.py",
     "locator = self.healed.get((test.name, step.locator), step.locator)", "locator = step.locator"),
]


def main() -> int:
    caught = 0
    for name, path, old, new in MUTANTS:
        file = pathlib.Path(path)
        source = file.read_text(encoding="utf-8")
        if old not in source:
            print(f"SKIPPED  {name}: the code changed, update this mutant")
            continue
        file.write_text(source.replace(old, new, 1), encoding="utf-8")
        try:
            result = subprocess.run([sys.executable, "-m", "pytest", "-x", "-q", "-m", "not browser",
                                     "-p", "no:cacheprovider"], capture_output=True, text=True)
        finally:
            file.write_text(source, encoding="utf-8")
        detected = result.returncode != 0
        caught += detected
        print(f"{'CAUGHT ' if detected else 'MISSED '} {name}")
    print(f"\n{caught}/{len(MUTANTS)} deliberate bugs were caught")
    return 0 if caught == len(MUTANTS) else 1


if __name__ == "__main__":
    sys.exit(main())
