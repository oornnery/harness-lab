from __future__ import annotations

from src.hooks import build_harness_hooks


def test_build_harness_hooks_registers_expected_entries():
    hooks = build_harness_hooks()
    registry = getattr(hooks, "_entries", None) or getattr(hooks, "entries", None)
    summary = repr(hooks) if registry is None else str(registry)
    for name in (
        "run_event_stream",
        "before_tool_execute",
        "after_tool_execute",
        "tool_execute_error",
        "model_request_error",
        "prepare_tools",
    ):
        assert name in summary
