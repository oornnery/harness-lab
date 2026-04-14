from __future__ import annotations

import pytest
from pydantic_ai.models.test import TestModel
from rich.console import Console

from src.agent import AgentBuilder
from src.cli.turn import TurnRunner
from src.cli.ui.renderer import StreamRenderer
from src.model import HarnessSettings, ModelAdapter
from src.policy import HarnessDeps
from src.schema import FinalAnswer
from src.session import UnifiedStore


def _builder(settings: HarnessSettings, store: UnifiedStore) -> AgentBuilder:
    return AgentBuilder(settings, ModelAdapter(settings), store)


def _runner(settings: HarnessSettings) -> TurnRunner:
    renderer = StreamRenderer(Console(record=True), settings)
    return TurnRunner(renderer, settings)


@pytest.fixture
def test_model() -> TestModel:
    return TestModel(
        call_tools=[],
        custom_output_args={
            "summary": "Deterministic test summary with enough length.",
            "reasoning_summary": "Synthetic reasoning for the unit test.",
            "files_considered": [],
            "actions": [],
            "next_steps": [],
        },
    )


async def test_turn_runs_with_test_model(
    harness_deps: HarnessDeps,
    harness_settings: HarnessSettings,
    session_store: UnifiedStore,
    test_model: TestModel,
):
    builder = _builder(harness_settings, session_store)
    handle = builder.setup(harness_deps, history=[])
    runner = _runner(harness_settings)

    with handle.agent.override(model=test_model):
        result = await runner.run(handle, "hello")

    assert result is not None
    assert isinstance(result.output, FinalAnswer)
    assert "test summary" in result.output.summary


async def test_turn_clears_repeat_guard(
    harness_deps: HarnessDeps,
    harness_settings: HarnessSettings,
    session_store: UnifiedStore,
    test_model: TestModel,
):
    builder = _builder(harness_settings, session_store)
    handle = builder.setup(harness_deps, history=[])
    runner = _runner(harness_settings)
    handle.deps.policy.recent_calls["stale"] = None

    with handle.agent.override(model=test_model):
        await runner.run(handle, "hello")

    assert "stale" not in handle.deps.policy.recent_calls
