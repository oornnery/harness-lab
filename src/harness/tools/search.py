"""Text search tools.

Provides tools for searching text across files in the workspace.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from pydantic_ai import RunContext

from src.policy import HarnessDeps

from .file import BINARY_EXTENSIONS
from .policy import PolicyGuard


class SearchTools:
    """Text search operations."""

    def __init__(self, policy_guard: PolicyGuard, deps: HarnessDeps) -> None:
        self.policy = policy_guard
        self.deps = deps
        self.root = deps.workspace.root

    async def search_text(
        self,
        ctx: RunContext[HarnessDeps],
        query: str,
        path: str = ".",
        limit: int | None = None,
    ) -> list[str]:
        """Search for text across files in the workspace.

        Args:
            ctx: Run context
            query: Case-insensitive text to search for.
            path: Relative path to search under.
            limit: Maximum number of matching lines.

        Returns:
            List of matching lines with file references
        """
        self.policy.guard_repeat(
            "search_text",
            {"query": query, "path": path, "limit": limit},
        )
        search_root = self.policy.resolve_path(path)
        limit = limit or self.deps.settings.max_search_hits

        # Try ripgrep first (10-100x faster for large repos)
        try:
            return await self._search_with_ripgrep(query, search_root, limit)
        except FileNotFoundError:
            # ripgrep not installed, fall back to Python implementation
            return await self._search_python(query, search_root, limit)

    async def _search_with_ripgrep(self, query: str, search_root: Path, limit: int) -> list[str]:
        """Search using ripgrep binary."""

        proc = await asyncio.create_subprocess_exec(
            "rg",
            "-i",
            "-n",
            "--no-heading",
            "--max-count",
            str(limit),
            query,
            str(search_root),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=str(self.root),
        )
        stdout, _ = await proc.communicate()
        if proc.returncode == 0:
            results = stdout.decode("utf-8", errors="ignore").splitlines()
            return results[:limit]
        return []

    async def _search_python(self, query: str, search_root: Path, limit: int) -> list[str]:
        """Search using pure Python implementation (fallback)."""
        results: list[str] = []
        for file in search_root.rglob("*"):
            if len(results) >= limit:
                break
            if file.is_dir() or self.policy.skip_path(file):
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
