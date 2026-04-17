import json
import secrets
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import TypedDict

from .agent import Agent, ChatMessage, ChatParams

SESSIONS_DIR = Path.home() / ".simple" / "sessions"
TRANSCRIPT = Path.home() / ".simple" / "transcript.jsonl"


@dataclass
class SessionState:
    stream_mode: bool = True
    params: ChatParams = field(default_factory=ChatParams)


class SessionUsage(TypedDict):
    prompt_tokens: int
    completion_tokens: int
    reasoning_tokens: int
    total_tokens: int


class SessionData(TypedDict):
    id: str
    created_at: str
    model: str
    instructions: str
    params: ChatParams
    messages: list[ChatMessage]
    usage: SessionUsage
    turns: int


def _session_dict(agent: Agent, state: SessionState, sid: str, created_at: str) -> SessionData:
    u = agent.session_usage
    return SessionData(
        id=sid,
        created_at=created_at,
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


def autosave_session(agent: Agent, state: SessionState) -> Path | None:
    if agent.turns == 0:
        return None
    SESSIONS_DIR.mkdir(parents=True, exist_ok=True)
    sid = secrets.token_hex(3)
    created_at = datetime.now(UTC).isoformat()
    path = SESSIONS_DIR / f"{sid}.json"
    path.write_text(json.dumps(_session_dict(agent, state, sid, created_at), indent=2, default=str))
    return path


def list_sessions() -> list[Path]:
    SESSIONS_DIR.mkdir(parents=True, exist_ok=True)
    return sorted(SESSIONS_DIR.glob("*.json"), key=lambda p: json.loads(p.read_text()).get("created_at", ""))


def load_session(sid: str, agent: Agent, state: SessionState) -> bool:
    path = SESSIONS_DIR / f"{sid}.json"
    if not path.exists():
        return False
    _apply_session(json.loads(path.read_text()), agent, state)
    return True


def reset_sessions() -> int:
    SESSIONS_DIR.mkdir(parents=True, exist_ok=True)
    count = 0
    for f in SESSIONS_DIR.glob("*.json"):
        f.unlink()
        count += 1
    return count


def log_turn(role: str, content: str) -> None:
    TRANSCRIPT.parent.mkdir(parents=True, exist_ok=True)
    with TRANSCRIPT.open("a") as f:
        f.write(json.dumps({"ts": datetime.now(UTC).isoformat(), "role": role, "content": content}) + "\n")
