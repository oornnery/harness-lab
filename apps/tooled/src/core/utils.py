import logging

from rich.console import Console
from rich.logging import RichHandler
from rich.progress import Progress, SpinnerColumn, TextColumn, TimeElapsedColumn

console = Console()

_log_console = Console(stderr=True)


def thinking_progress(label: str = "Thinking...") -> Progress:
    return Progress(
        SpinnerColumn(),
        TextColumn(f"[bold yellow]{label}[/bold yellow]"),
        TimeElapsedColumn(),
        console=console,
        transient=True,
    )


logging.basicConfig(
    level=logging.NOTSET,
    format="%(message)s",
    datefmt="[%X]",
    handlers=[
        RichHandler(
            console=_log_console,
            rich_tracebacks=True,
            markup=False,
            show_path=False,
            show_time=True,
            omit_repeated_times=True,
        )
    ],
    force=True,
)

logger = logging.getLogger("rich")

__all__ = ["console", "logger", "thinking_progress"]
