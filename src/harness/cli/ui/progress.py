"""Progress tracking for agent runs."""

from __future__ import annotations

from rich.console import Console
from rich.progress import Progress, SpinnerColumn, TaskID, TextColumn


class RunProgress:
    """Track agent execution progress."""

    def __init__(self, console: Console) -> None:
        self.console = console
        self._progress: Progress | None = None
        self._task: TaskID | None = None
        self._active = False

    def start(self, label: str) -> None:
        self._progress = Progress(
            SpinnerColumn(style="cyan"),
            TextColumn("[progress.description]{task.description}"),
            TextColumn("[dim]({task.elapsed:.1f}s)[/]"),
            console=self.console,
            transient=True,
        )
        self._task = self._progress.add_task(label, total=None)
        self._progress.start()
        self._active = True

    def stop(self) -> None:
        if self._progress is not None and self._active:
            self._progress.stop()
            self._active = False

    def update(self, label: str) -> None:
        if self._progress is None:
            return
        if self._task is not None:
            self._progress.update(self._task, description=label)
        if not self._active:
            self._progress.start()
            self._active = True

    def reset(self) -> None:
        self.stop()
        self._progress = None
        self._task = None
