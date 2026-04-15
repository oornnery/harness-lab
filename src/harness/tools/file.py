"""File operation tools.

Provides tools for listing, reading, writing, and modifying files.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from pydantic_ai import ModelRetry, RunContext

from src.policy import HarnessDeps

from ._clip import clip
from .policy import PolicyGuard

# Binary file extensions that should be skipped in text operations
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


class FileTools:
    """File system operations."""

    def __init__(self, policy_guard: PolicyGuard, deps: HarnessDeps) -> None:
        self.policy = policy_guard
        self.deps = deps
        self.root = deps.workspace.root
        # path -> (mtime_ns, full_content). Invalidated by mtime change.
        self._read_cache: dict[Path, tuple[int, str]] = {}

    async def list_files(
        self, ctx: RunContext[HarnessDeps], path: str = ".", limit: int = 200
    ) -> list[str]:
        """List files under a workspace path.

        Args:
            ctx: Run context
            path: Relative path inside the workspace.
            limit: Maximum number of files to return.

        Returns:
            List of relative file paths
        """
        self.policy.guard_repeat("list_files", {"path": path, "limit": limit})
        root = self.policy.resolve_path(path)
        return await asyncio.to_thread(self._list_files_sync, root, limit)

    def _read_cached(self, file_path: Path) -> str:
        """Read full file content, reusing cache when mtime is unchanged."""
        mtime = file_path.stat().st_mtime_ns
        cached = self._read_cache.get(file_path)
        if cached is not None and cached[0] == mtime:
            return cached[1]
        content = file_path.read_text(encoding="utf-8", errors="ignore")
        self._read_cache[file_path] = (mtime, content)
        return content

    def _invalidate_cache(self, file_path: Path) -> None:
        self._read_cache.pop(file_path, None)

    def _list_files_sync(self, root: Path, limit: int) -> list[str]:
        """Synchronous file listing implementation."""
        results: list[str] = []
        for file in root.rglob("*"):
            if len(results) >= limit:
                break
            if file.is_dir() or self.policy.skip_path(file):
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
            ctx: Run context
            path: Relative path inside the workspace.
            start_line: First line to read.
            end_line: Last line to read.

        Returns:
            File content with line numbers
        """
        self.policy.guard_repeat(
            "read_file",
            {"path": path, "start_line": start_line, "end_line": end_line},
        )
        file_path = self.policy.resolve_path(path)

        max_lines = self.deps.settings.max_file_lines
        start = max(start_line, 1)
        end = end_line or (start + max_lines - 1)
        if end - start + 1 > max_lines:
            end = start + max_lines - 1

        try:
            content = await asyncio.to_thread(self._read_cached, file_path)
        except (OSError, UnicodeDecodeError):
            raise ModelRetry(f"File not found or unreadable: {path}") from None

        ctx.deps.working_memory.touch_file(path)
        lines = content.splitlines()
        selected = lines[start - 1 : end]
        rendered = [f"{idx:>4}: {line}" for idx, line in enumerate(selected, start=start)]
        return "\n".join(rendered) if rendered else "<empty selection>"

    async def write_file(self, ctx: RunContext[HarnessDeps], path: str, content: str) -> str:
        """Create or replace a file.

        Args:
            ctx: Run context
            path: Relative path inside the workspace.
            content: Full file content.

        Returns:
            Success message
        """
        self.policy.guard_repeat("write_file", {"path": path, "content": content[:200]})
        file_path = self.policy.resolve_path(path)
        self.policy.check_write_allowed(file_path)
        self.policy.require_write_approval(ctx, file_path, path)

        await asyncio.to_thread(file_path.parent.mkdir, parents=True, exist_ok=True)
        await asyncio.to_thread(file_path.write_text, content, encoding="utf-8")
        self._invalidate_cache(file_path)
        ctx.deps.working_memory.touch_file(path)
        self.policy.record_mutation("write_file", {"path": path})
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
            ctx: Run context
            path: Relative path inside the workspace.
            old: Existing text that must be found.
            new: Replacement text.
            expected_replacements: Expected number of matches.

        Returns:
            Diff or success message
        """
        import difflib

        self.policy.guard_repeat(
            "replace_in_file",
            {
                "path": path,
                "old": old[:200],
                "new": new[:200],
                "expected_replacements": expected_replacements,
            },
        )
        file_path = self.policy.resolve_path(path)
        self.policy.check_write_allowed(file_path)
        self.policy.require_write_approval(ctx, file_path, path)

        original = await asyncio.to_thread(self._read_cached, file_path)
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
        self._invalidate_cache(file_path)
        ctx.deps.working_memory.touch_file(path)

        diff = "\n".join(
            difflib.unified_diff(
                original.splitlines(),
                updated.splitlines(),
                fromfile=f"a/{path}",
                tofile=f"b/{path}",
                lineterm="",
            )
        )
        self.policy.record_mutation("replace_in_file", {"path": path})
        return clip(diff, 4_000) or f"Updated {path}"
