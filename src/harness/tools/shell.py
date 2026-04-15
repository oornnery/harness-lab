"""Shell execution tools.

Provides tools for executing shell commands.
"""

from __future__ import annotations

import asyncio

from pydantic_ai import ModelRetry, RunContext
from src.policy import HarnessDeps

from ._clip import clip
from .policy import PolicyGuard


class ShellTools:
    """Shell command execution."""

    def __init__(self, policy_guard: PolicyGuard, deps: HarnessDeps) -> None:
        self.policy = policy_guard
        self.deps = deps
        self.root = deps.workspace.root

    async def execute(
        self,
        ctx: RunContext[HarnessDeps],
        command: str,
        timeout: int | None = None,
    ) -> str:
        """Run a shell command inside the workspace.

        Args:
            ctx: Run context
            command: Shell command to execute.
            timeout: Timeout in seconds.

        Returns:
            Command output with exit code
        """
        self.policy.guard_repeat("run_shell", {"command": command, "timeout": timeout})
        self.policy.check_shell_allowed(command)
        self.policy.require_shell_approval(ctx, command)

        timeout = timeout or self.deps.settings.shell_timeout_seconds
        proc = await asyncio.create_subprocess_shell(
            command,
            cwd=str(self.root),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

        try:
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        except TimeoutError:
            proc.kill()
            await proc.wait()
            raise ModelRetry(f"Shell command timed out after {timeout} seconds.") from None

        output = stdout.decode("utf-8", errors="ignore")
        err = stderr.decode("utf-8", errors="ignore")
        self.policy.record_mutation(
            "run_shell", {"command": command, "returncode": proc.returncode}
        )

        return (
            f"exit_code={proc.returncode}\n"
            f"stdout:\n{clip(output, 4000) or '<empty>'}\n\n"
            f"stderr:\n{clip(err, 2000) or '<empty>'}"
        )
