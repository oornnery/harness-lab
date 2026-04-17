import readline
from pathlib import Path

from rich.console import Console

from .agent import Agent

HISTORY_FILE = Path.home() / ".simple" / "history"


def init_readline() -> None:
    HISTORY_FILE.parent.mkdir(parents=True, exist_ok=True)
    if HISTORY_FILE.exists():
        readline.read_history_file(HISTORY_FILE)
    readline.set_history_length(1000)


def save_history() -> None:
    readline.write_history_file(HISTORY_FILE)


def read_input(console: Console) -> str:
    line = console.input("[bold blue]You:[/bold blue] ")
    if not line.endswith("\\"):
        return line.strip()
    buf = [line.rstrip("\\")]
    while True:
        cont = console.input("[dim]... [/dim]")
        if not cont.endswith("\\"):
            buf.append(cont)
            break
        buf.append(cont.rstrip("\\"))
    return "\n".join(buf).strip()


def cancel_turn(agent: Agent) -> str:
    if agent.messages and agent.messages[-1].get("role") == "user":
        agent.messages.pop()
    return "[dim]cancelled[/dim]"
