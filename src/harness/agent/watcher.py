"""Hot-reload personas when `src/prompts/*.md` changes on disk."""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import Awaitable, Callable
from pathlib import Path

from watchfiles import awatch

from .personas import clear_persona_cache, prompts_dir

ReloadCallback = Callable[[set[Path]], Awaitable[None]]


class PersonaWatcher:
    """Background task that invalidates `load_persona` cache on file changes."""

    def __init__(self, on_reload: ReloadCallback | None = None) -> None:
        self._on_reload = on_reload
        self._task: asyncio.Task[None] | None = None
        self._stop_event = asyncio.Event()

    def start(self) -> None:
        if self._task is not None:
            return
        self._stop_event.clear()
        self._task = asyncio.create_task(self._run(), name="persona-watcher")

    async def stop(self) -> None:
        if self._task is None:
            return
        self._stop_event.set()
        self._task.cancel()
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await self._task
        self._task = None

    async def _run(self) -> None:
        async for changes in awatch(prompts_dir(), stop_event=self._stop_event):
            md_paths = {Path(path) for _, path in changes if Path(path).suffix == ".md"}
            if not md_paths:
                continue
            clear_persona_cache()
            if self._on_reload is not None:
                with contextlib.suppress(Exception):
                    await self._on_reload(md_paths)
