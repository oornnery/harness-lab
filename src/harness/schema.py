"""Harness output schema: pure domain types."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Literal

from pydantic import BaseModel, Field
from pydantic_ai import (
    DeferredToolRequests,
    ModelRetry,
    NativeOutput,
    RunContext,
    ToolOutput,
)

if TYPE_CHECKING:
    from .policy import HarnessDeps

_DEFAULT_REASONING = "The result was assembled from tool outputs and workspace context."


class ActionRecord(BaseModel):
    kind: Literal["read", "search", "write", "edit", "shell", "approval", "other"]
    summary: str


class FinalAnswer(BaseModel):
    """Structured final result for the coding harness."""

    summary: str = Field(description="Direct answer to the user's request.")
    reasoning_summary: str = Field(description="Short explanation of how the answer was reached.")
    files_considered: list[str] = Field(default_factory=list)
    actions: list[ActionRecord] = Field(default_factory=list)
    next_steps: list[str] = Field(default_factory=list)


HarnessOutput = FinalAnswer | DeferredToolRequests


def build_output_types(native: bool = False) -> list[Any]:
    """Build the output union for the agent."""
    wrapper = NativeOutput(FinalAnswer) if native else ToolOutput(FinalAnswer)
    return [wrapper, DeferredToolRequests]


def _normalize_files(raw: list[str]) -> list[str]:
    normalized: list[str] = []
    for item in raw:
        value = item.strip().lstrip("/")
        if value:
            normalized.append(value)
    return list(dict.fromkeys(normalized))


class HarnessOutputValidator:
    """Output validator callable used via `agent.output_validator(...)`."""

    def __init__(self, min_summary_chars: int = 10) -> None:
        self.min_summary_chars = min_summary_chars

    async def __call__(self, ctx: RunContext[HarnessDeps], data: HarnessOutput) -> HarnessOutput:
        if isinstance(data, DeferredToolRequests):
            return data

        if not data.summary.strip():
            raise ModelRetry("The final answer must include a non-empty summary.")

        if len(data.summary) < self.min_summary_chars:
            raise ModelRetry("The summary is too short; provide a useful result.")

        data.files_considered = _normalize_files(data.files_considered)
        if not data.reasoning_summary.strip():
            data.reasoning_summary = _DEFAULT_REASONING

        return data
