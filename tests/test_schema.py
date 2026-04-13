from __future__ import annotations

from typing import Any, cast

import pytest
from pydantic_ai import DeferredToolRequests, ModelRetry, RunContext

from src.schema import FinalAnswer, HarnessOutputValidator, build_output_types

_FAKE_CTX = cast(RunContext[Any], None)


async def test_validator_rejects_empty_summary():
    validator = HarnessOutputValidator()
    bad = FinalAnswer(summary="", reasoning_summary="r")
    with pytest.raises(ModelRetry):
        await validator(_FAKE_CTX, bad)


async def test_validator_rejects_short_summary():
    validator = HarnessOutputValidator()
    bad = FinalAnswer(summary="short", reasoning_summary="r")
    with pytest.raises(ModelRetry):
        await validator(_FAKE_CTX, bad)


async def test_validator_normalizes_files_and_fills_reasoning():
    validator = HarnessOutputValidator()
    data = FinalAnswer(
        summary="a sufficient summary",
        reasoning_summary="",
        files_considered=["/foo.py", " bar.py", "foo.py", ""],
    )
    result = await validator(_FAKE_CTX, data)
    assert isinstance(result, FinalAnswer)
    assert result.files_considered == ["foo.py", "bar.py"]
    assert "tool outputs" in result.reasoning_summary


async def test_validator_passes_deferred_unchanged():
    validator = HarnessOutputValidator()
    deferred = DeferredToolRequests()
    result = await validator(_FAKE_CTX, deferred)
    assert result is deferred


def test_build_output_types_native_flag():
    tool = build_output_types(native=False)
    native = build_output_types(native=True)
    assert type(tool[0]).__name__ == "ToolOutput"
    assert type(native[0]).__name__ == "NativeOutput"
