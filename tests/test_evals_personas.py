"""Behavioral evals for personas using pydantic_evals + TestModel.

These evals use `TestModel` with custom output args so they stay fast
and offline. Real LLM evals can be added later by overriding the
builder's model and removing `call_tools=[]`.
"""

from __future__ import annotations

from typing import Any, cast

import pytest
from pydantic_ai.models.test import TestModel
from pydantic_evals import Case, Dataset
from pydantic_evals.evaluators import IsInstance

from src.agent import AgentBuilder
from src.model import HarnessSettings, ModelAdapter
from src.policy import HarnessDeps
from src.schema import FinalAnswer
from src.session import UnifiedStore


async def _run_turn_with_test_model(
    deps: HarnessDeps,
    settings: HarnessSettings,
    store: UnifiedStore,
    persona: str,
    prompt: str,
    summary: str,
) -> FinalAnswer | str:
    builder = AgentBuilder(settings, ModelAdapter(settings), store)
    handle = builder.setup(deps, history=[], persona_name=persona)
    model = TestModel(
        call_tools=[],
        custom_output_args={
            "summary": summary,
            "reasoning_summary": "synthetic reasoning",
            "files_considered": [],
            "actions": [],
            "next_steps": [],
        },
    )
    with handle.agent.override(model=model):
        result = await handle.agent.run(prompt, deps=deps)
    output = result.output
    if isinstance(output, FinalAnswer):
        return output
    return type(output).__name__


@pytest.fixture
def persona_dataset() -> Any:
    cases = cast(
        Any,
        [
            Case(
                name="agents_greeting",
                inputs="hello",
                metadata={"persona": "AGENTS"},
            ),
            Case(
                name="coder_simple_task",
                inputs="refactor the foo module",
                metadata={"persona": "coder"},
            ),
            Case(
                name="planner_ambiguous_request",
                inputs="make the harness better",
                metadata={"persona": "planner"},
            ),
            Case(
                name="reviewer_diff_request",
                inputs="review the changes on main",
                metadata={"persona": "reviewer"},
            ),
        ],
    )
    return Dataset(
        name="personas_behavioral_baseline",
        cases=cases,
        evaluators=[IsInstance(type_name="FinalAnswer")],
    )


async def test_personas_eval_dataset(
    persona_dataset: Any,
    harness_deps: HarnessDeps,
    harness_settings: HarnessSettings,
    session_store: UnifiedStore,
):
    async def task(prompt: str) -> FinalAnswer | str:
        persona = "AGENTS"
        for case in persona_dataset.cases:
            if case.inputs == prompt:
                persona = case.metadata.get("persona", "AGENTS")
                break
        return await _run_turn_with_test_model(
            harness_deps,
            harness_settings,
            session_store,
            persona=persona,
            prompt=prompt,
            summary=f"Deterministic response for {persona}.",
        )

    report = await persona_dataset.evaluate(task)
    assert len(report.cases) == 4
    for case in report.cases:
        assert all(assertion.value is True for assertion in case.assertions.values()), (
            f"case {case.name} failed: {case.assertions}"
        )
