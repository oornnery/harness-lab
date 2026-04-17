import json
import secrets
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import TypedDict

from .agent import Agent, ChatMessage, ChatParams, ChatUsage
from .utils import logger

SIMPLE_HOME = Path.cwd() / ".simple"
SESSIONS_DIR = SIMPLE_HOME / "sessions"
EXPORTS_DIR = SIMPLE_HOME / "exports"
TRANSCRIPT = SIMPLE_HOME / "transcript.jsonl"


@dataclass
class SessionState:
    stream_mode: bool = True
    params: ChatParams = field(default_factory=ChatParams)
    current_id: str | None = None
    pending_input: str | None = None
    prefill: str = ""


class SessionUsage(TypedDict):
    prompt_tokens: int
    completion_tokens: int
    reasoning_tokens: int
    total_tokens: int


class SessionData(TypedDict):
    id: str
    created_at: str
    updated_at: str
    model: str
    instructions: str
    params: ChatParams
    messages: list[ChatMessage]
    usage: SessionUsage
    turns: int


def _new_session_id() -> str:
    return secrets.token_hex(3)


def _safe_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return {}


def _session_dict(agent: Agent, state: SessionState, sid: str, created_at: str) -> SessionData:
    u = agent.session_usage
    return SessionData(
        id=sid,
        created_at=created_at,
        updated_at=datetime.now(UTC).isoformat(),
        model=agent.config.model,
        instructions=agent.config.instructions,
        params=ChatParams(**state.params),
        messages=agent.messages,
        usage=SessionUsage(
            prompt_tokens=u.prompt_tokens,
            completion_tokens=u.completion_tokens,
            reasoning_tokens=u.reasoning_tokens,
            total_tokens=u.total_tokens,
        ),
        turns=agent.turns,
    )


def _apply_session(data: SessionData, agent: Agent, state: SessionState) -> None:
    agent.config.model = data["model"]
    agent.config.instructions = data.get("instructions", "")
    state.params = data.get("params", {})
    agent.messages = data["messages"]
    agent.turns = data.get("turns", 0)
    usage = data.get("usage")
    if usage:
        agent.session_usage = ChatUsage(
            prompt_tokens=usage.get("prompt_tokens", 0),
            completion_tokens=usage.get("completion_tokens", 0),
            reasoning_tokens=usage.get("reasoning_tokens", 0),
            total_tokens=usage.get("total_tokens", 0),
        )
    state.current_id = data.get("id")


def ensure_session_id(state: SessionState) -> str:
    if state.current_id is None:
        state.current_id = _new_session_id()
    return state.current_id


def autosave_session(agent: Agent, state: SessionState) -> Path | None:
    if agent.turns == 0:
        return None
    SESSIONS_DIR.mkdir(parents=True, exist_ok=True)
    sid = ensure_session_id(state)
    path = SESSIONS_DIR / f"{sid}.json"
    now_iso = datetime.now(UTC).isoformat()
    created_at = _safe_json(path).get("created_at", now_iso) if path.exists() else now_iso
    path.write_text(json.dumps(_session_dict(agent, state, sid, created_at), indent=2, default=str))
    logger.debug("session saved id=%s turns=%d path=%s", sid, agent.turns, path)
    return path


def list_sessions() -> list[Path]:
    SESSIONS_DIR.mkdir(parents=True, exist_ok=True)
    return sorted(SESSIONS_DIR.glob("*.json"), key=lambda p: _safe_json(p).get("created_at", ""))


def latest_session_id() -> str | None:
    """Return the id of the most recently updated session, or None."""
    SESSIONS_DIR.mkdir(parents=True, exist_ok=True)
    files = list(SESSIONS_DIR.glob("*.json"))
    if not files:
        return None
    latest = max(files, key=lambda p: _safe_json(p).get("updated_at") or _safe_json(p).get("created_at") or "")
    return latest.stem


def load_session(sid: str, agent: Agent, state: SessionState) -> bool:
    path = SESSIONS_DIR / f"{sid}.json"
    if not path.exists():
        return False
    _apply_session(json.loads(path.read_text()), agent, state)
    logger.info("session loaded id=%s turns=%d", sid, agent.turns)
    return True


def reset_sessions() -> int:
    SESSIONS_DIR.mkdir(parents=True, exist_ok=True)
    count = 0
    for f in SESSIONS_DIR.glob("*.json"):
        f.unlink()
        count += 1
    return count


def log_turn(
    role: str,
    content: str,
    *,
    session_id: str | None = None,
    model: str | None = None,
    usage: ChatUsage | None = None,
    response_time: float | None = None,
) -> None:
    TRANSCRIPT.parent.mkdir(parents=True, exist_ok=True)
    entry: dict = {
        "ts": datetime.now(UTC).isoformat(),
        "role": role,
        "content": content,
    }
    if session_id is not None:
        entry["session_id"] = session_id
    if model is not None:
        entry["model"] = model
    if usage is not None:
        entry["tokens"] = usage.to_dict()
    if response_time is not None:
        entry["response_time"] = response_time
    with TRANSCRIPT.open("a") as f:
        f.write(json.dumps(entry) + "\n")


def export_markdown(agent: Agent, state: SessionState, dest: Path | None = None) -> Path:
    if dest is None:
        sid = ensure_session_id(state)
        EXPORTS_DIR.mkdir(parents=True, exist_ok=True)
        dest = EXPORTS_DIR / f"{sid}.md"
    dest.parent.mkdir(parents=True, exist_ok=True)
    lines: list[str] = [
        f"# Session {state.current_id or 'unsaved'}",
        "",
        f"- Model: `{agent.config.model}`",
        f"- Turns: {agent.turns}",
        f"- Total tokens: {agent.session_usage.total_tokens}",
        "",
    ]
    for msg in agent.messages:
        role = msg.get("role", "?")
        content = msg.get("content") or ""
        lines.append(f"## {role}")
        lines.append("")
        lines.append(content)
        lines.append("")
    dest.write_text("\n".join(lines))
    return dest
