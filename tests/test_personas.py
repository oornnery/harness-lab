from __future__ import annotations

import pytest

from src.agent import list_personas, load_persona
from src.agent.personas import clear_persona_cache, prompts_dir


def test_load_agents_persona():
    doc = load_persona("AGENTS")
    assert doc.name == "AGENTS"
    assert "coding-agent harness" in doc.content
    assert doc.metadata["default_mode"] == "read-write"


def test_load_nonexistent_raises():
    with pytest.raises(FileNotFoundError):
        load_persona("no_such_persona")


def test_list_personas_excludes_underscore():
    names = [p.name for p in list_personas()]
    assert "AGENTS" in names
    assert "coder" in names
    assert "planner" in names
    assert "reviewer" in names
    assert not any(n.startswith("_") for n in names)


def test_reviewer_thinking_medium():
    doc = load_persona("reviewer")
    assert doc.metadata["thinking"] == "medium"
    assert doc.metadata["output_retries"] == 3


def test_coder_base_chain_prepends_agents():
    coder = load_persona("coder")
    agents = load_persona("AGENTS")
    assert coder.content.startswith(agents.content[:50])
    assert "Coder-specific" in coder.content
    assert "base" not in coder.metadata
    assert coder.metadata["default_mode"] == "read-write"


def test_planner_delegates_list():
    doc = load_persona("planner")
    assert doc.metadata["delegates"] == ["coder", "reviewer"]


def test_clear_persona_cache_forces_reload():
    load_persona("AGENTS")  # populate cache
    before = load_persona.cache_info()
    assert before.currsize >= 1
    clear_persona_cache()
    after = load_persona.cache_info()
    assert after.currsize == 0
    # Repopulate so other tests keep hitting the cache.
    load_persona("AGENTS")


def test_prompts_dir_points_at_prompts_folder():
    path = prompts_dir()
    assert path.name == "prompts"
    assert (path / "agents" / "AGENTS.md").exists()
