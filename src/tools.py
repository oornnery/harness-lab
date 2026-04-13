from __future__ import annotations

import asyncio
import difflib
from pathlib import Path

from pydantic_ai import ApprovalRequired, ModelRetry, RunContext, Tool

from .policy import HarnessDeps

BINARY_EXTENSIONS: frozenset[str] = frozenset(
    {
        ".png",
        ".jpg",
        ".jpeg",
        ".gif",
        ".webp",
        ".ico",
        ".bmp",
        ".tiff",
        ".pdf",
        ".zip",
        ".tar",
        ".gz",
        ".bz2",
        ".xz",
        ".7z",
        ".rar",
        ".exe",
        ".dll",
        ".so",
        ".dylib",
        ".class",
        ".jar",
        ".pyc",
        ".pyo",
        ".sqlite",
        ".sqlite3",
        ".db",
        ".mdb",
        ".mp3",
        ".mp4",
        ".wav",
        ".ogg",
        ".flac",
        ".avi",
        ".mov",
        ".mkv",
        ".webm",
        ".woff",
        ".woff2",
        ".ttf",
        ".otf",
        ".eot",
        ".bin",
        ".dat",
        ".iso",
        ".img",
    }
)


class ToolRuntime:
    """Real tools for the coding-agent harness.

    Tools are bound methods with `ctx: RunContext[HarnessDeps]` as the first
    parameter. `as_tools()` wraps them into `Tool` objects that the agent
    registers directly -- no module-level `register_tools` helper.
    """

    def __init__(self, deps: HarnessDeps) -> None:
        self.deps = deps
        self.root = deps.workspace.root

    async def list_files(
        self, ctx: RunContext[HarnessDeps], path: str = ".", limit: int = 200
    ) -> list[str]:
        """List files under a workspace path.

        Args:
            path: Relative path inside the workspace.
            limit: Maximum number of files to return.
        """
        ctx.deps.policy.guard_repeat("list_files", {"path": path, "limit": limit})
        root = self.deps.policy.resolve_path(path)
        return await asyncio.to_thread(self._list_files_sync, root, limit)

    def _list_files_sync(self, root: Path, limit: int) -> list[str]:
        results: list[str] = []
        for file in root.rglob("*"):
            if len(results) >= limit:
                break
            if file.is_dir() or self.deps.policy.skip_path(file):
                continue
            results.append(str(file.relative_to(self.root)))
        return results

    async def read_file(
        self,
        ctx: RunContext[HarnessDeps],
        path: str,
        start_line: int = 1,
        end_line: int | None = None,
    ) -> str:
        """Read a file with line numbers.

        Args:
            path: Relative path inside the workspace.
            start_line: First line to read.
            end_line: Last line to read.
        """
        ctx.deps.policy.guard_repeat(
            "read_file",
            {"path": path, "start_line": start_line, "end_line": end_line},
        )
        file_path = self.deps.policy.resolve_path(path)
        if not file_path.exists() or not file_path.is_file():
            raise ModelRetry(f"File not found: {path}")

        max_lines = self.deps.settings.max_file_lines
        start = max(start_line, 1)
        end = end_line or (start + max_lines - 1)
        if end - start + 1 > max_lines:
            end = start + max_lines - 1

        content = await asyncio.to_thread(file_path.read_text, "utf-8", "ignore")
        lines = content.splitlines()
        selected = lines[start - 1 : end]
        rendered = [f"{idx:>4}: {line}" for idx, line in enumerate(selected, start=start)]
        return "\n".join(rendered) if rendered else "<empty selection>"

    async def search_text(
        self,
        ctx: RunContext[HarnessDeps],
        query: str,
        path: str = ".",
        limit: int | None = None,
    ) -> list[str]:
        """Search for text across files in the workspace.

        Args:
            query: Case-insensitive text to search for.
            path: Relative path to search under.
            limit: Maximum number of matching lines.
        """
        ctx.deps.policy.guard_repeat(
            "search_text",
            {"query": query, "path": path, "limit": limit},
        )
        search_root = self.deps.policy.resolve_path(path)
        limit = limit or self.deps.settings.max_search_hits
        results: list[str] = []

        for file in search_root.rglob("*"):
            if len(results) >= limit:
                break
            if file.is_dir() or self.deps.policy.skip_path(file):
                continue
            if file.suffix.lower() in BINARY_EXTENSIONS:
                continue
            try:
                text = await asyncio.to_thread(file.read_text, "utf-8", "ignore")
            except OSError:
                continue
            for line_number, line in enumerate(text.splitlines(), start=1):
                if query.lower() in line.lower():
                    rel = file.relative_to(self.root)
                    results.append(f"{rel}:{line_number}: {line.strip()}")
                    if len(results) >= limit:
                        break
        return results

    def _require_write_approval(
        self, ctx: RunContext[HarnessDeps], file_path: Path, raw_path: str
    ) -> None:
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

    async def write_file(self, ctx: RunContext[HarnessDeps], path: str, content: str) -> str:
        """Create or replace a file.

        Args:
            path: Relative path inside the workspace.
            content: Full file content.
        """
        ctx.deps.policy.guard_repeat("write_file", {"path": path, "content": content[:200]})
        file_path = self.deps.policy.resolve_path(path)
        self.deps.policy.check_write_allowed(file_path)
        self._require_write_approval(ctx, file_path, path)

        await asyncio.to_thread(file_path.parent.mkdir, parents=True, exist_ok=True)
        await asyncio.to_thread(file_path.write_text, content, encoding="utf-8")
        self.deps.policy.record_mutation("write_file", {"path": path})
        return f"Wrote {len(content)} bytes to {file_path.relative_to(self.root)}"

    async def replace_in_file(
        self,
        ctx: RunContext[HarnessDeps],
        path: str,
        old: str,
        new: str,
        expected_replacements: int = 1,
    ) -> str:
        """Replace exact text inside a file.

        Args:
            path: Relative path inside the workspace.
            old: Existing text that must be found.
            new: Replacement text.
            expected_replacements: Expected number of matches. If > 0, the call
                fails unless exactly that many occurrences are found, and only
                that many are replaced. Pass 0 to replace all occurrences without
                count validation.
        """
        ctx.deps.policy.guard_repeat(
            "replace_in_file",
            {
                "path": path,
                "old": old[:200],
                "new": new[:200],
                "expected_replacements": expected_replacements,
            },
        )
        file_path = self.deps.policy.resolve_path(path)
        self.deps.policy.check_write_allowed(file_path)
        self._require_write_approval(ctx, file_path, path)

        original = await asyncio.to_thread(file_path.read_text, "utf-8", "ignore")
        occurrences = original.count(old)
        if occurrences == 0:
            raise ModelRetry(f"Pattern not found in {path!r}.")
        if expected_replacements > 0 and occurrences != expected_replacements:
            raise ModelRetry(
                f"Expected {expected_replacements} occurrence(s) of the target text in "
                f"{path!r}, found {occurrences}."
            )

        count = expected_replacements if expected_replacements > 0 else -1
        updated = original.replace(old, new, count)
        await asyncio.to_thread(file_path.write_text, updated, encoding="utf-8")

        diff = "\n".join(
            difflib.unified_diff(
                original.splitlines(),
                updated.splitlines(),
                fromfile=f"a/{path}",
                tofile=f"b/{path}",
                lineterm="",
            )
        )
        self.deps.policy.record_mutation("replace_in_file", {"path": path})
        return diff[:4_000] or f"Updated {path}"

    async def run_shell(
        self,
        ctx: RunContext[HarnessDeps],
        command: str,
        timeout: int | None = None,
    ) -> str:
        """Run a shell command inside the workspace.

        Args:
            command: Shell command to execute.
            timeout: Timeout in seconds.
        """
        ctx.deps.policy.guard_repeat("run_shell", {"command": command, "timeout": timeout})
        self.deps.policy.check_shell_allowed(command)
        if not ctx.tool_call_approved:
            raise ApprovalRequired(metadata={"reason": "shell", "command": command})

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
        self.deps.policy.record_mutation(
            "run_shell", {"command": command, "returncode": proc.returncode}
        )

        return (
            f"exit_code={proc.returncode}\n"
            f"stdout:\n{output[:4000] or '<empty>'}\n\n"
            f"stderr:\n{err[:2000] or '<empty>'}"
        )

    def as_tools(self) -> list[Tool[HarnessDeps]]:
        return [
            Tool(self.list_files, metadata={"category": "read"}),
            Tool(self.read_file, metadata={"category": "read"}),
            Tool(self.search_text, metadata={"category": "read"}),
            Tool(self.write_file, metadata={"category": "mutate"}),
            Tool(self.replace_in_file, metadata={"category": "mutate"}),
            Tool(
                self.run_shell,
                metadata={"category": "shell"},
                requires_approval=True,
                timeout=float(self.deps.settings.shell_timeout_seconds),
            ),
        ]
