import readline
from collections.abc import Callable
from pathlib import Path

from .agent import Agent, Thinking
from .commands import PARAM_PARSERS, known_commands
from .session import list_sessions

HISTORY_FILE = Path.cwd() / ".tooled" / "history"
HEREDOC_TAG = "<<<"


def _ansi(text: str, code: str) -> str:
    return f"\x01\x1b[{code}m\x02{text}\x01\x1b[0m\x02"


PROMPT_YOU = _ansi("You:", "1;34") + " "
PROMPT_CONT = _ansi("...", "2") + " "

_agent_ref: Agent | None = None


def init_readline(agent: Agent | None = None) -> None:
    global _agent_ref
    _agent_ref = agent
    HISTORY_FILE.parent.mkdir(parents=True, exist_ok=True)
    if HISTORY_FILE.exists():
        readline.read_history_file(HISTORY_FILE)
    readline.set_history_length(1000)
    readline.set_completer(_complete)
    readline.set_completer_delims(" \t\n")
    if "libedit" in (readline.__doc__ or ""):
        readline.parse_and_bind("bind ^I rl_complete")
    else:
        readline.parse_and_bind("tab: complete")


def save_history() -> None:
    readline.write_history_file(HISTORY_FILE)


def _arg_candidates(cmd: str) -> list[str]:
    if cmd == "/session":
        return [p.stem for p in list_sessions()] + ["reset"]
    if cmd == "/thinking":
        return [*(t.value for t in Thinking), "off"]
    if cmd == "/set":
        return list(PARAM_PARSERS)
    if cmd == "/compact":
        return ["undo"]
    if cmd == "/memory":
        return ["list", "recall", "add", "clear", "forget"]
    if cmd == "/policy":
        return ["show", "allow", "confirm", "deny"]
    return []


def _complete(text: str, idx: int) -> str | None:
    buf = readline.get_line_buffer()
    if not buf.lstrip().startswith("/"):
        return None
    begidx = readline.get_begidx()
    prefix_tokens = buf[:begidx].split()
    if not prefix_tokens:
        candidates = [c for c in known_commands() if c.startswith(text)]
    else:
        cmd = prefix_tokens[0].lower()
        candidates = [c for c in _arg_candidates(cmd) if c.startswith(text)]
    candidates = sorted(set(candidates))
    if idx < len(candidates):
        return candidates[idx]
    return None


def _prefill_hook(text: str) -> Callable[[], None]:
    def hook() -> None:
        readline.insert_text(text)
        readline.redisplay()

    return hook


def read_input(prefill: str = "") -> str:
    if prefill:
        readline.set_startup_hook(_prefill_hook(prefill))
    try:
        line = input(PROMPT_YOU)
    finally:
        if prefill:
            readline.set_startup_hook()

    stripped = line.strip()
    if stripped == HEREDOC_TAG:
        return _read_heredoc(HEREDOC_TAG)
    if stripped.startswith(HEREDOC_TAG + " "):
        tag = stripped[len(HEREDOC_TAG) + 1 :].strip() or HEREDOC_TAG
        return _read_heredoc(tag)
    if not line.endswith("\\"):
        return stripped
    buf = [line.rstrip("\\")]
    while True:
        cont = input(PROMPT_CONT)
        if not cont.endswith("\\"):
            buf.append(cont)
            break
        buf.append(cont.rstrip("\\"))
    return "\n".join(buf).strip()


def _read_heredoc(tag: str) -> str:
    prompt = _ansi(f"{tag}>", "2") + " "
    lines: list[str] = []
    while True:
        line = input(prompt)
        if line.strip() == tag:
            break
        lines.append(line)
    return "\n".join(lines).strip()


def cancel_turn(agent: Agent) -> str:
    if agent.messages and agent.messages[-1].get("role") == "user":
        agent.messages.pop()
    return "[dim]cancelled[/dim]"
