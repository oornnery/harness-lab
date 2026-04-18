from __future__ import annotations

import asyncio

from ..core.tool import tool

_OUTPUT_LIMIT = 50 * 1024  # 50 KB


@tool(name="shell", desc="Run a shell command and return its output (stdout + stderr).", timeout=60.0)
async def shell(cmd: str, timeout: float = 30.0) -> str:
    proc = await asyncio.create_subprocess_shell(
        cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )
    try:
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except TimeoutError:
        proc.kill()
        await proc.communicate()
        return f"Error: command timed out after {timeout}s"

    output = stdout.decode("utf-8", errors="replace")
    if len(output) > _OUTPUT_LIMIT:
        output = output[:_OUTPUT_LIMIT] + f"\n... [truncated at {_OUTPUT_LIMIT // 1024} KB]"

    exit_code = proc.returncode or 0
    if exit_code != 0:
        return f"Exit {exit_code}:\n{output}"
    return output
