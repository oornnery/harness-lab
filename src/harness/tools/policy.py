"""Policy enforcement for tool execution.

Checks if tool execution is allowed based on policy settings.
"""

from __future__ import annotations

from pathlib import Path

from pydantic_ai import ApprovalRequired, RunContext

from src.policy import HarnessDeps


class PolicyGuard:
    """Check if tool execution is allowed.

    Wraps policy checks for tool execution, including
    read-only mode, write approval, and shell execution approval.
    """

    def __init__(self, deps: HarnessDeps) -> None:
        self.deps = deps
        self.root = deps.workspace.root

    def guard_repeat(self, tool_name: str, args: dict) -> None:
        """Guard against repeated tool calls with same arguments.

        Args:
            tool_name: Name of the tool being called
            args: Arguments passed to the tool
        """
        self.deps.policy.guard_repeat(tool_name, args)

    def resolve_path(self, path: str) -> Path:
        """Resolve a path relative to the workspace root.

        Args:
            path: Relative path string

        Returns:
            Resolved absolute Path
        """
        return self.deps.policy.resolve_path(path)

    def skip_path(self, path: Path) -> bool:
        """Check if a path should be skipped.

        Args:
            path: Path to check

        Returns:
            True if path should be skipped
        """
        return self.deps.policy.skip_path(path)

    def requires_write_approval(self, file_path: Path) -> bool:
        """Check if writing to a file requires approval.

        Args:
            file_path: Path to check

        Returns:
            True if approval is required
        """
        return self.deps.policy.requires_write_approval(file_path)

    def requires_protected_approval(self, file_path: Path) -> bool:
        """Check if file is protected and requires special approval.

        Args:
            file_path: Path to check

        Returns:
            True if file is protected
        """
        return self.deps.policy.requires_protected_approval(file_path)

    def check_write_allowed(self, file_path: Path) -> None:
        """Check if write operation is allowed.

        Args:
            file_path: Path to check

        Raises:
            PermissionError: If write is not allowed
        """
        self.deps.policy.check_write_allowed(file_path)

    def check_shell_allowed(self, command: str) -> None:
        """Check if shell command execution is allowed.

        Args:
            command: Command to check

        Raises:
            PermissionError: If shell is not allowed
        """
        self.deps.policy.check_shell_allowed(command)

    def record_mutation(self, tool_name: str, args: dict) -> None:
        """Record a mutation operation.

        Args:
            tool_name: Name of the tool that caused mutation
            args: Arguments passed to the tool
        """
        self.deps.policy.record_mutation(tool_name, args)

    def require_write_approval(
        self, ctx: RunContext[HarnessDeps], file_path: Path, raw_path: str
    ) -> None:
        """Require approval for write operation if needed.

        Args:
            ctx: Run context
            file_path: Resolved file path
            raw_path: Original path string

        Raises:
            ApprovalRequired: If approval is required but not granted
        """
        if not self.deps.policy.requires_write_approval(file_path):
            return
        if ctx.tool_call_approved:
            return

        reason = (
            "protected-file"
            if self.deps.policy.requires_protected_approval(file_path)
            else "manual-approval"
        )
        raise ApprovalRequired(metadata={"reason": reason, "path": raw_path})

    def require_shell_approval(self, ctx: RunContext[HarnessDeps], command: str) -> None:
        """Require approval for shell execution.

        Args:
            ctx: Run context
            command: Command to execute

        Raises:
            ApprovalRequired: If approval is required but not granted
        """
        if ctx.tool_call_approved:
            return
        raise ApprovalRequired(metadata={"reason": "shell", "command": command})
