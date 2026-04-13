from __future__ import annotations

import asyncio
import os
from pathlib import Path

from pydantic import BaseModel, Field

from .model import HarnessSettings

IGNORED_DIRS: frozenset[str] = frozenset(
    {
        ".git",
        ".venv",
        "node_modules",
        "dist",
        "build",
        "__pycache__",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
        ".harness",
    }
)


class WorkspaceContext(BaseModel):
    root: Path
    branch: str | None = None
    default_branch: str | None = None
    git_status: str | None = None
    recent_commits: list[str] = Field(default_factory=list)
    guidance_files: dict[str, str] = Field(default_factory=dict)
    sampled_files: list[str] = Field(default_factory=list)

    def prompt_summary(self) -> str:
        parts: list[str] = [f"Workspace root: {self.root}"]
        if self.branch:
            parts.append(f"Current branch: {self.branch}")
        if self.default_branch:
            parts.append(f"Default branch: {self.default_branch}")
        if self.git_status:
            parts.append("Git status:\n" + self.git_status)
        if self.recent_commits:
            parts.append("Recent commits:\n- " + "\n- ".join(self.recent_commits))
        if self.guidance_files:
            rendered = []
            for name, content in self.guidance_files.items():
                rendered.append(f"## {name}\n{content}")
            parts.append("Project guidance files:\n" + "\n\n".join(rendered))
        if self.sampled_files:
            parts.append("Representative files:\n- " + "\n- ".join(self.sampled_files))
        return "\n\n".join(parts)

    def short_summary(self) -> str:
        status = self.branch or "no-git"
        return f"{self.root.name} | branch={status} | files={len(self.sampled_files)}"


class ContextBuilder:
    """Collects workspace context similar to the snapshot step in coding agents."""

    GUIDANCE_FILES = (
        "AGENTS.md",
        "CLAUDE.md",
        "README.md",
        "CONTRIBUTING.md",
    )

    def __init__(self, settings: HarnessSettings) -> None:
        self.settings = settings
        self.root = settings.resolved_workspace()

    async def build(self) -> WorkspaceContext:
        guidance_task = asyncio.create_task(self._load_guidance_files())
        samples_task = asyncio.create_task(self._sample_files())

        branch = None
        default_branch = None
        git_status = None
        recent_commits: list[str] = []

        if self.settings.include_git_context:
            branch = await self._git("rev-parse", "--abbrev-ref", "HEAD")
            default_branch = await self._git("symbolic-ref", "refs/remotes/origin/HEAD")
            if default_branch and "/" in default_branch:
                default_branch = default_branch.rsplit("/", 1)[-1]
            git_status = await self._git("status", "--short")
            commits = await self._git("log", "--oneline", "-5")
            if commits:
                recent_commits = [line for line in commits.splitlines() if line.strip()]

        return WorkspaceContext(
            root=self.root,
            branch=branch,
            default_branch=default_branch,
            git_status=git_status,
            recent_commits=recent_commits,
            guidance_files=await guidance_task,
            sampled_files=await samples_task,
        )

    async def _git(self, *args: str) -> str | None:
        proc = await asyncio.create_subprocess_exec(
            "git",
            *args,
            cwd=str(self.root),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        stdout, _ = await proc.communicate()
        if proc.returncode != 0:
            return None
        value = stdout.decode("utf-8", errors="ignore").strip()
        return value or None

    async def _load_guidance_files(self) -> dict[str, str]:
        loaded: dict[str, str] = {}
        for name in self.GUIDANCE_FILES:
            path = self.root / name
            if path.exists() and path.is_file():
                loaded[name] = await self._read_excerpt(path, max_chars=4_000)
        return loaded

    async def _sample_files(self) -> list[str]:
        return await asyncio.to_thread(self._sample_files_sync)

    def _sample_files_sync(self) -> list[str]:
        results: list[str] = []
        allowed = {".py", ".md", ".toml", ".yaml", ".yml", ".json"}
        for current, dirs, files in os.walk(self.root):
            dirs[:] = [d for d in dirs if d not in IGNORED_DIRS]
            for name in files:
                if Path(name).suffix.lower() not in allowed:
                    continue
                rel = str(Path(current, name).relative_to(self.root))
                results.append(rel)
                if len(results) >= 30:
                    return results
        return results

    async def _read_excerpt(self, path: Path, *, max_chars: int) -> str:
        content = await asyncio.to_thread(path.read_text, "utf-8", "ignore")
        clipped = content[:max_chars]
        if len(content) > max_chars:
            clipped += "\n\n...[truncated]"
        return clipped
