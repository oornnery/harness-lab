from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field
from pydantic_ai import Agent, DeferredToolRequests, ModelRetry, PromptedOutput, RunContext

from .policy import HarnessDeps


class ActionRecord(BaseModel):
    kind: Literal["read", "search", "write", "edit", "shell", "approval", "other"]
    summary: str


class FinalAnswer(BaseModel):
    """Structured final result for the coding harness.

    Tool output mode is used on purpose because it coexists naturally with the rest
    of the function tools and deferred approval flow.
    """

    summary: str = Field(description="Direct answer to the user's request.")
    reasoning_summary: str = Field(description="Short explanation of how the answer was reached.")
    files_considered: list[str] = Field(default_factory=list)
    actions: list[ActionRecord] = Field(default_factory=list)
    next_steps: list[str] = Field(default_factory=list)


HarnessOutput = FinalAnswer | DeferredToolRequests


def build_output_types() -> list[Any]:
    return [PromptedOutput(FinalAnswer), DeferredToolRequests]


def register_output_validator(agent: Agent[HarnessDeps, HarnessOutput]) -> None:
    @agent.output_validator
    async def _validate_output(ctx: RunContext[HarnessDeps], data: HarnessOutput) -> HarnessOutput:
        if isinstance(data, DeferredToolRequests):
            return data

        if not data.summary.strip():
            raise ModelRetry("The final answer must include a non-empty summary.")

        if len(data.summary) < 10:
            raise ModelRetry("The summary is too short; provide a useful result.")

        normalized_files = []
        for item in data.files_considered:
            value = item.strip().lstrip("/")
            if value:
                normalized_files.append(value)

        data.files_considered = list(dict.fromkeys(normalized_files))
        if not data.reasoning_summary.strip():
            data.reasoning_summary = (
                "The result was assembled from tool outputs and workspace context."
            )

        return data
