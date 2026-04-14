from __future__ import annotations

import time

import pytest
from rich.console import Console

from src.cli.commands.base import ExtensionState
from src.cli.commands.logs import logs_command
from src.model import HarnessSettings
from src.policy import HarnessDeps
from src.session import UnifiedStore


@pytest.fixture
async def seeded_state(
    harness_deps: HarnessDeps,
    session_store: UnifiedStore,
    harness_settings: HarnessSettings,
) -> ExtensionState:
    sid = harness_deps.session_id
    await session_store.append_event(sid, {"kind": "tool-call", "tool": "read_file"})
    await session_store.append_event(sid, {"kind": "tool-call", "tool": "run_shell"})
    await session_store.append_event(sid, {"kind": "tool-result", "tool": "read_file"})
    await session_store.append_event(sid, {"kind": "model-request-start", "model": "test"})
    return ExtensionState(
        console=Console(record=True),
        deps=harness_deps,
        session_store=session_store,
        known_tools=[],
        workspace_summary="",
    )


async def test_query_events_filters_by_kind(
    seeded_state: ExtensionState, session_store: UnifiedStore
):
    events = await session_store.query_events(seeded_state.session_id, kinds=["tool-call"])
    assert len(events) == 2
    assert all(e["kind"] == "tool-call" for e in events)


async def test_query_events_filters_by_tool(
    seeded_state: ExtensionState, session_store: UnifiedStore
):
    events = await session_store.query_events(seeded_state.session_id, tool_name="read_file")
    kinds = {e["kind"] for e in events}
    assert kinds == {"tool-call", "tool-result"}


async def test_query_events_since_filter_future_returns_empty(
    seeded_state: ExtensionState, session_store: UnifiedStore
):
    future = int(time.time()) + 3600
    events = await session_store.query_events(seeded_state.session_id, since_ts=future)
    assert events == []


async def test_logs_command_renders_table(seeded_state: ExtensionState):
    await logs_command(seeded_state, "kind=tool-call last=10")
    output = seeded_state.console.export_text()
    assert "tool-call" in output
    assert "read_file" in output or "run_shell" in output
