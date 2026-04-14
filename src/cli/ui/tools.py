"""Tool call visualization."""

from __future__ import annotations

import json
from typing import Any

from rich.console import Console
from rich.text import Text

# Tree glyphs
_TREE_BRANCH = "├─"
_TREE_LAST = "└─"
_TREE_CONT = "│ "


class ToolCallRenderer:
    """Render tool calls in a tree structure."""

    def __init__(self, console: Console) -> None:
        self.console = console
        self._counter = 0

    def render_tool_call(self, tool: str, args: Any, tool_call_id: str, prefix: str = "") -> None:
        self._counter += 1
        prefix_label = f"{prefix} #{self._counter}" if prefix else f"#{self._counter}"

        args_str = self._format_tool_args(args)
        self.console.print(
            Text.assemble(
                (f"  {prefix_label} ", "cyan"),
                (tool, "bold yellow"),
                ("  ", ""),
                (args_str, "dim"),
            )
        )

    def render_tool_result(self, result: Any, tool_call_id: str) -> None:
        result_repr = repr(result.content)
        truncated = self._truncate_value(result_repr, limit=100)
        self.console.print(
            Text.assemble(
                (f"    {_TREE_BRANCH} result ", "green"),
                (truncated, "dim"),
            )
        )

    def _format_tool_args(self, args: Any) -> str:
        if isinstance(args, str):
            try:
                args_data = json.loads(args)
            except json.JSONDecodeError:
                return self._truncate_value(args, limit=40)
        elif isinstance(args, dict):
            args_data = args
        else:
            return self._truncate_value(repr(args), limit=40)

        if isinstance(args_data, dict):
            path = args_data.get("path", "")
            if path:
                return str(path)

        return self._truncate_value(json.dumps(args_data, ensure_ascii=False), limit=40)

    def _truncate_value(self, value: Any, limit: int = 40) -> str:
        value_str = str(value)
        if len(value_str) <= limit:
            return value_str
        return value_str[: limit - 1] + "…"

    def reset_counter(self) -> None:
        self._counter = 0
