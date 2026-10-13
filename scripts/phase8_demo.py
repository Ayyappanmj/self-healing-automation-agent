"""Phase 8 demo: consult the optional LLM on borderline cases.

Run from the project root:  python -m scripts.phase8_demo
Reads the demo pages' HTML directly (no browser needed). Live-browser integration comes in Phase 9.

- LLM_PROVIDER=none (default): shows the deterministic result and the exact prompt that WOULD be sent.
- LLM_PROVIDER=ollama / gemini: really calls the model and shows how its answer is combined.
"""
from pathlib import Path

from agent.candidate_scorer import LOW_CONFIDENCE_MESSAGE, build_profile, score_candidates
from agent.config import load_settings
from agent.dom_inspector import parse_html
from agent.llm_reasoner import (
    LLMUnavailable, combine, get_provider, reason_about_failure, select_prompt_candidates, should_consult_llm,
)
from agent.locator_generator import generate_candidates

VERSIONS = Path(__file__).resolve().parent.parent / "demo_site" / "versions"
CASES = [
    ("v3", "click", "a#forgot", "Click forgot password"),
    ("v4", "click", "a#forgot", "Click forgot password"),
    ("v3", "get_text", "#welcome", "Read welcome message"),
    ("v2", "click", "button#login", "Click login"),          # confident: LLM not needed
]

if __name__ == "__main__":
    s = load_settings()
    try:
        provider = get_provider(s)
    except LLMUnavailable as exc:
        print(f"LLM not usable: {exc}. Falling back to deterministic mode.")
        provider = None
    print(f"LLM provider: {provider.name + ' / ' + provider.model if provider else 'none (deterministic mode)'}")
    previewed = False
    for version, action, locator, name in CASES:
        els = parse_html((VERSIONS / f"{version}.html").read_text(encoding="utf-8"))
        profile = build_profile(locator, action, name)
        result = score_candidates(generate_candidates(els, action), profile)
        print(f"\n=== {version}: {name} ({locator}) ===")
        print(f"deterministic: conf {result.confidence:.2f} -> {result.decision(s.confidence_threshold)}")
        if not should_consult_llm(result, s.confidence_threshold):
            print("LLM not consulted (deterministic result is already confident, or there is no evidence).")
            continue
        if provider is None:
            if not previewed:
                from agent.llm_reasoner import build_prompt
                print("LLM disabled. Prompt that would be sent:\n---")
                print(build_prompt(profile, select_prompt_candidates(result), failure_type="locator_not_found"))
                print("---\nEnable Ollama in .env (LLM_PROVIDER=ollama) to try it.")
                previewed = True
            continue
        llm = reason_about_failure(provider, profile, result, failure_type="locator_not_found",
                                   timeout_s=s.llm_timeout_s)
        print(f"LLM status: {llm.status} ({llm.latency_ms} ms, {llm.attempts} attempt(s))")
        if llm.error:
            print(f"  note: {llm.error}")
        if llm.decision and llm.decision.valid:
            print(f"  LLM said: healed={llm.decision.healed} locator={llm.decision.locator} "
                  f"conf={llm.decision.confidence} reason={llm.decision.reason!r}")
        proposal = combine(result, llm)
        verdict = "HEAL" if proposal.should_heal(s.confidence_threshold) else "MANUAL REVIEW"
        print(f"final: {proposal.locator or '-'}  conf {proposal.confidence:.2f}  source={proposal.source}  -> {verdict}")
        if verdict != "HEAL":
            print(f"  {LOW_CONFIDENCE_MESSAGE}")
