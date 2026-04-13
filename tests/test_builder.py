from __future__ import annotations

from src.agent import AgentBuilder
from src.model import HarnessSettings, ModelAdapter
from src.policy import HarnessDeps
from src.sessions import SessionStore


def _builder(settings: HarnessSettings, store: SessionStore) -> AgentBuilder:
    return AgentBuilder(settings, ModelAdapter(settings), store)


async def test_setup_builds_handle(
    harness_deps: HarnessDeps,
    harness_settings: HarnessSettings,
    session_store: SessionStore,
):
    builder = _builder(harness_settings, session_store)
    handle = builder.setup(harness_deps, history=[])
    assert handle.persona.name == "AGENTS"
    assert handle.deps is harness_deps
    assert handle.deps.persona_meta["default_mode"] == "read-write"


async def test_rebuild_switches_persona(
    harness_deps: HarnessDeps,
    harness_settings: HarnessSettings,
    session_store: SessionStore,
):
    builder = _builder(harness_settings, session_store)
    handle = builder.setup(harness_deps, history=[])
    new_handle = builder.rebuild(handle, "planner")
    assert new_handle.persona.name == "planner"
    assert new_handle.deps is handle.deps
    assert new_handle.deps.persona_meta.get("thinking") == "high"


async def test_rebuild_preserves_runtime_identity(
    harness_deps: HarnessDeps,
    harness_settings: HarnessSettings,
    session_store: SessionStore,
):
    builder = _builder(harness_settings, session_store)
    handle = builder.setup(harness_deps, history=[])
    runtime_id = id(handle.runtime)
    new_handle = builder.rebuild(handle, "coder")
    assert id(new_handle.runtime) == runtime_id
