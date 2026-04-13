from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from pydantic_ai import ApprovalRequired, ModelRetry

from src.policy import HarnessDeps
from src.tools import ToolRuntime


def _fake_ctx(deps: HarnessDeps, approved: bool = False) -> MagicMock:
    ctx = MagicMock()
    ctx.deps = deps
    ctx.tool_call_approved = approved
    return ctx


async def test_list_files_returns_relative(harness_deps: HarnessDeps):
    runtime = ToolRuntime(harness_deps)
    ctx = _fake_ctx(harness_deps)
    result = await runtime.list_files(ctx, path=".", limit=10)
    assert any("hello.txt" in f for f in result)


async def test_read_file_not_found(harness_deps: HarnessDeps):
    runtime = ToolRuntime(harness_deps)
    ctx = _fake_ctx(harness_deps)
    with pytest.raises(ModelRetry):
        await runtime.read_file(ctx, path="nope.txt")


async def test_write_file_readonly_blocked(harness_deps: HarnessDeps):
    harness_deps.settings.read_only = True
    runtime = ToolRuntime(harness_deps)
    ctx = _fake_ctx(harness_deps, approved=True)
    with pytest.raises(ModelRetry):
        await runtime.write_file(ctx, path="new.txt", content="x")


async def test_run_shell_requires_approval(harness_deps: HarnessDeps):
    runtime = ToolRuntime(harness_deps)
    ctx = _fake_ctx(harness_deps, approved=False)
    with pytest.raises(ApprovalRequired):
        await runtime.run_shell(ctx, command="echo hi")


async def test_guard_repeat_blocks_second_call(harness_deps: HarnessDeps):
    runtime = ToolRuntime(harness_deps)
    ctx = _fake_ctx(harness_deps)
    await runtime.list_files(ctx, path=".", limit=5)
    with pytest.raises(ModelRetry):
        await runtime.list_files(ctx, path=".", limit=5)


async def test_guard_repeat_unblocks_after_clear(harness_deps: HarnessDeps):
    runtime = ToolRuntime(harness_deps)
    ctx = _fake_ctx(harness_deps)
    await runtime.list_files(ctx, path=".", limit=5)
    harness_deps.policy.recent_calls.clear()
    await runtime.list_files(ctx, path=".", limit=5)
