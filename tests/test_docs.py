"""Guards for the documentation: required sections, valid links, honest claims, documented settings."""
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
README = (ROOT / "README.md").read_text(encoding="utf-8")
DOCS = [ROOT / "README.md", ROOT / "docs" / "architecture.md", ROOT / "docs" / "testing.md", ROOT / "docs" / "demo-recording.md",
        ROOT / "docs" / "github-setup.md", ROOT / "docs" / "benchmarks.md", ROOT / "docs" / "deployment.md",
        ROOT / "CHANGELOG.md", ROOT / "RUN_GUIDE.md"]

REQUIRED_SECTIONS = ["Demo", "Problem", "Solution", "Features", "Architecture", "Tech stack", "How it works",
                     "Installation", "Run", "Run tests", "Configuration", "Project structure", "Limitations",
                     "Future improvements", "License"]


@pytest.mark.parametrize("section", REQUIRED_SECTIONS)
def test_readme_has_required_section(section):
    assert re.search(rf"^## {re.escape(section)}\s*$", README, re.M), section


def test_readme_leads_with_what_why_how_result():
    head = README[:2500]
    for word in ("**What**", "**Why**", "**How**", "**Result**"):
        assert word in head


def test_demo_gif_is_only_a_placeholder_until_it_exists():
    embedded = re.search(r"^!\[[^\]]*\]\(docs/demo\.gif\)", README, re.M)
    if not (ROOT / "docs" / "demo.gif").exists():
        assert embedded is None, "README embeds docs/demo.gif but the file does not exist"
        assert "has not been added yet" in README


def test_windows_install_commands_present():
    for cmd in ("python -m venv .venv", r".venv\Scripts\activate", "pip install -r requirements.txt",
                "playwright install firefox", "streamlit run app.py", "pytest"):
        assert cmd in README


def test_no_unmeasured_numbers_are_claimed():
    lowered = README.lower()
    assert not re.search(r"\d+\s*%\s*(test )?coverage|coverage[^.\n]{0,25}\d+\s*%", lowered)
    assert not re.search(r"(reduc|improv|sav)\w*[^.\n]{0,40}\d+\s*%", lowered)
    assert not re.search(r"\d+(\.\d+)?\s*%\s*(success|healing|accuracy|recovery)", lowered)


def test_every_env_variable_is_documented_in_the_readme():
    keys = re.findall(r"^([A-Z_]+)=", (ROOT / ".env.example").read_text(encoding="utf-8"), re.M)
    assert keys
    missing = [k for k in keys if f"`{k}`" not in README]
    assert missing == []


def test_gemini_api_key_is_documented():
    assert "GEMINI_API_KEY` | empty" in README


def test_documented_defaults_match_the_code():
    from agent.config import Settings
    s = Settings()
    expected = {"CONFIDENCE_THRESHOLD": s.confidence_threshold, "MAX_HEALING_ATTEMPTS": s.max_healing_attempts,
                "ACTION_TIMEOUT_MS": s.action_timeout_ms, "ACTION_RETRIES": s.action_retries,
                "LLM_TIMEOUT_S": s.llm_timeout_s, "DB_PATH": s.db_path, "LOG_DIR": s.log_dir,
                "SCREENSHOT_DIR": s.screenshot_dir, "OLLAMA_MODEL": s.ollama_model}
    for key, value in expected.items():
        row = re.search(rf"^\| `{key}` \| `?([^|`]+)`? \|", README, re.M)
        assert row and row.group(1).strip() == str(value), key


@pytest.mark.parametrize("doc", DOCS, ids=lambda p: p.name)
def test_relative_links_point_to_real_files(doc):
    text = doc.read_text(encoding="utf-8")
    text = re.sub(r"```.*?```", "", text, flags=re.S)           # example snippets are not real links
    text = re.sub(r"<!--.*?-->", "", text, flags=re.S)           # commented-out placeholders
    for target in re.findall(r"\]\(([^)#\s]+)\)", text):
        if target.startswith(("http://", "https://", "mailto:")):
            continue
        assert (doc.parent / target).exists(), f"{doc.name} links to missing {target}"


@pytest.mark.parametrize("doc", DOCS, ids=lambda p: p.name)
def test_code_fences_are_balanced(doc):
    assert doc.read_text(encoding="utf-8").count("```") % 2 == 0


def mermaid_blocks():
    for doc in DOCS:
        for block in re.findall(r"```mermaid\n(.*?)```", doc.read_text(encoding="utf-8"), re.S):
            yield doc.name, block


def test_mermaid_diagrams_exist_and_look_well_formed():
    blocks = list(mermaid_blocks())
    assert any(name == "README.md" for name, _ in blocks) and any(name == "architecture.md" for name, _ in blocks)
    for name, block in blocks:
        first = block.strip().splitlines()[0]
        assert re.match(r"(flowchart (TD|LR|TB|RL)|sequenceDiagram|graph (TD|LR))", first), (name, first)
        assert block.count("[") == block.count("]") and block.count("(") == block.count(")")
        assert block.count("{") == block.count("}")
        assert len(re.findall(r"^\s*subgraph\b", block, re.M)) == len(re.findall(r"^\s*end\s*$", block, re.M))


def test_every_module_named_in_the_architecture_table_exists():
    text = (ROOT / "docs" / "architecture.md").read_text(encoding="utf-8")
    for path in re.findall(r"`((?:agent|browser|storage|ui)/[\w./]*)`", text):
        assert (ROOT / path).exists(), path


def test_project_structure_in_readme_matches_the_repository():
    for path in ("app.py", "agent/controller.py", "agent/healing_engine.py", "agent/failure_detector.py",
                 "agent/dom_inspector.py", "agent/locator_generator.py", "agent/candidate_scorer.py",
                 "agent/llm_reasoner.py", "agent/locator_validator.py", "agent/runner.py", "agent/scenarios.py",
                 "agent/logging_setup.py", "agent/config.py", "storage/database.py", "docs/architecture.md",
                 "docs/testing.md", "RUN_GUIDE.md", "LICENSE"):
        assert (ROOT / path).exists(), path
        assert Path(path).name in README, path


def test_readme_does_not_contain_secrets_or_real_keys():
    assert not re.search(r"sk-[A-Za-z0-9]{10,}", README)
    assert "GEMINI_API_KEY` | empty" in README


# --- demo recording guide ------------------------------------------------------------------------
GUIDE = (ROOT / "docs" / "demo-recording.md").read_text(encoding="utf-8")


@pytest.mark.parametrize("scene", ["Dashboard", "Run Demo", "Browser opens", "locator failure", "DOM inspection",
                                   "candidate locators", "AI reasoning", "Healed locator", "retry", "Healing History"])
def test_recording_guide_covers_every_required_scene(scene):
    assert scene.lower() in GUIDE.lower(), scene


def test_recording_guide_keeps_the_honesty_rules_and_a_refusal_scene():
    assert "## Honesty rules" in GUIDE
    for phrase in ("No mock-ups", "invented numbers", "manual review", "Do not call it AI-reasoned unless it was"):
        assert phrase in GUIDE, phrase


def test_recording_guide_commands_and_helpers_exist():
    for path in ("scripts/demo_preflight.py", "scripts/make_gif.ps1"):
        assert (ROOT / path).exists()
    assert "python -m scripts.demo_preflight" in GUIDE and "make_gif.ps1" in GUIDE
    ps = (ROOT / "scripts" / "make_gif.ps1").read_text(encoding="utf-8")
    assert "palettegen" in ps and "paletteuse" in ps and "ffmpeg" in ps


def test_recordings_folder_is_git_ignored():
    assert "recordings/" in (ROOT / ".gitignore").read_text(encoding="utf-8")


def test_script_timings_are_increasing_and_fit_about_a_minute():
    times = [tuple(map(int, m.groups())) for m in re.finditer(r"^\| (\d+):(\d{2}) \|", GUIDE, re.M)]
    seconds = [m * 60 + s for m, s in times]
    assert len(seconds) >= 8 and seconds == sorted(seconds) and seconds[-1] <= 75


def test_recording_guide_warns_about_the_action_timeout_dead_air():
    assert "ACTION_TIMEOUT_MS" in GUIDE and "dead air" in GUIDE


# --- GitHub setup, deployment and CI ------------------------------------------------------------------------------
SETUP = (ROOT / "docs" / "github-setup.md").read_text(encoding="utf-8")


def fenced(text, heading, lang):
    section = text.split(f"## {heading}", 1)[1]
    return re.search(rf"```{lang}\n(.*?)```", section, re.S).group(1).strip()


def test_repository_description_fits_githubs_limit():
    desc = fenced(SETUP, "Repository description", "text")
    assert 80 < len(desc) <= 350, len(desc)


def test_topics_are_valid_github_topics():
    topics = [x.strip() for x in fenced(SETUP, "Topics", "text").split(",")]
    assert 1 <= len(topics) <= 20 and len(set(topics)) == len(topics)
    assert all(re.fullmatch(r"[a-z0-9][a-z0-9-]{0,49}", x) for x in topics)


def test_commit_plan_adds_only_real_paths_and_covers_every_file():
    from scripts.prepush_check import tracked_files
    plan = fenced(SETUP, "3. Commit structure", "powershell")
    added = [tok for line in plan.splitlines() if line.startswith("git add ") for tok in line.split()[2:]]
    assert added
    for path in added:
        assert (ROOT / path).exists(), f"commit plan adds a missing path: {path}"
    uncovered = []
    for rel in tracked_files(ROOT):
        if rel.startswith("benchmarks/results/") and not rel.endswith(".gitkeep"):
            continue                                            # generated results are your choice to commit
        if rel in ("showcase/showcase_run.json", ".coverage") or rel.startswith(("htmlcov/", ".pytest_cache/")):
            continue
        if not any(rel == a or rel.startswith(a.rstrip("/") + "/") for a in added):
            uncovered.append(rel)
    assert uncovered == [], f"files missing from the commit plan: {uncovered}"


def test_commit_messages_follow_conventional_commits():
    msgs = re.findall(r'git commit -m "([^"]+)"', SETUP)
    assert len(msgs) >= 10
    assert all(re.match(r"(feat|fix|docs|chore|ci|test|refactor)(\([a-z-]+\))?: .{10,}", m) for m in msgs), msgs


def test_ci_workflow_is_valid_yaml_and_runs_the_suite_with_a_browser():
    yaml = pytest.importorskip("yaml")
    wf = yaml.safe_load((ROOT / ".github" / "workflows" / "tests.yml").read_text(encoding="utf-8"))
    triggers = wf.get("on") or wf.get(True)                         # YAML turns a bare `on` key into True
    assert "push" in triggers and "pull_request" in triggers
    job = wf["jobs"]["test"]
    assert job["strategy"]["matrix"]["python-version"] == ["3.11", "3.12"]
    commands = " ".join(step.get("run", "") for step in job["steps"])
    assert "pip install -r requirements.txt" in commands and "playwright install --with-deps firefox" in commands
    assert "pytest" in commands


def test_readme_does_not_show_a_ci_badge_before_ci_has_run():
    assert "actions/workflows/tests.yml/badge.svg" not in README


def test_dockerfile_installs_the_browser_and_runs_headless():
    docker = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    assert "playwright install --with-deps firefox" in docker and "HEADLESS=true" in docker
    assert "--server.address=0.0.0.0" in docker and "EXPOSE 8501" in docker
    ignore = (ROOT / ".dockerignore").read_text(encoding="utf-8")
    assert ".env" in ignore.splitlines() and "data/" in ignore.splitlines()


def test_deployment_doc_is_honest_about_what_is_unverified():
    doc = (ROOT / "docs" / "deployment.md").read_text(encoding="utf-8")
    assert "I have not deployed this project to Streamlit Community Cloud" in doc
    assert "I have not built it" in doc
    assert "SHOWCASE_MODE" in doc and "showcase_run.json" in doc


def test_benchmark_doc_forbids_unmeasured_claims():
    doc = (ROOT / "docs" / "benchmarks.md").read_text(encoding="utf-8")
    assert "Do not quote a number about this project that you did not measure" in doc
    assert "maintenance time" in doc and not re.search(r"\b\d+(\.\d+)?\s*%", doc)
