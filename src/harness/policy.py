from __future__ import annotations

import hashlib
import json
from collections import OrderedDict, deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from src.background import BackgroundRunner

from pydantic_ai import (
    ModelRetry,
)
from src.memory import MemoryStore
from src.session import UnifiedStore

from .context import IGNORED_DIRS, WorkspaceContext
from .model import HarnessSettings, ModelAdapter

REPEAT_GUARD_MAX = 256


@dataclass
class RuntimePolicy:
    settings: HarnessSettings
    workspace_root: Path
    protected_names: tuple[str, ...] = (".env", ".gitignore", "pyproject.toml")
    dangerous_shell_fragments: tuple[str, ...] = (
        "rm -rf /",
        "shutdown",
        "reboot",
        "mkfs",
        ":(){ :|:& };:",
    )
    recent_calls: OrderedDict[str, None] = field(default_factory=OrderedDict)
    mutations: list[dict[str, Any]] = field(default_factory=list)
    tool_timings: dict[str, float] = field(default_factory=dict)

    def resolve_path(self, raw_path: str) -> Path:
        path = (self.workspace_root / raw_path).resolve()
        if self.workspace_root != path and self.workspace_root not in path.parents:
            raise ModelRetry(f"Path escapes the workspace sandbox: {raw_path}")
        return path

    def clear_session(self) -> None:
        """Clear accumulated state (recent_calls, mutations, tool_timings).

        Call this when switching sessions or after long-running operations
        to prevent unbounded memory growth.
        """
        self.recent_calls.clear()
        self.mutations.clear()
        self.tool_timings.clear()

    def skip_path(self, path: Path) -> bool:
        return bool(set(path.parts) & IGNORED_DIRS)

    def requires_protected_approval(self, path: Path) -> bool:
        return path.name in self.protected_names

    def requires_write_approval(self, path: Path) -> bool:
        if self.settings.approval_mode == "manual":
            return True
        return path.name in self.protected_names

    def check_write_allowed(self, path: Path) -> None:
        if self.settings.read_only:
            raise ModelRetry("The workspace is in read-only mode; write operations are disabled.")
        self.resolve_path(str(path.relative_to(self.workspace_root)))

    def check_shell_allowed(self, command: str) -> None:
        lowered = command.lower()
        if self.settings.read_only and any(
            token in lowered for token in ("rm ", "mv ", "sed -i", ">")
        ):
            raise ModelRetry("The workspace is read-only; mutating shell commands are disabled.")
        for fragment in self.dangerous_shell_fragments:
            if fragment in lowered:
                raise ModelRetry(f"Refusing dangerous shell fragment: {fragment}")

    def _fingerprint(self, tool_name: str, payload: dict[str, Any]) -> str:
        data = json.dumps({"tool": tool_name, "payload": payload}, sort_keys=True, default=str)
        return hashlib.sha256(data.encode("utf-8")).hexdigest()

    def guard_repeat(self, tool_name: str, payload: dict[str, Any]) -> None:
        fingerprint = self._fingerprint(tool_name, payload)
        if fingerprint in self.recent_calls:
            raise ModelRetry(
                f"The same {tool_name!r} call was already attempted. "
                "Try a different tool or new arguments."
            )
        self.recent_calls[fingerprint] = None
        while len(self.recent_calls) > REPEAT_GUARD_MAX:
            self.recent_calls.popitem(last=False)

    def record_mutation(self, kind: str, payload: dict[str, Any]) -> None:
        self.mutations.append({"kind": kind, "payload": payload})


_MAX_FILES = 8
_MAX_NOTES = 5
_NOTE_KEY_LEN = 60


@dataclass
class WorkingMemory:
    """In-session scratch pad rendered into the agent prompt every turn.

    Distinct from `MemoryStore` (long-term semantic SQLite). This one
    holds the active task, the few last touched files, and short notes
    the model explicitly captures via `save_note`.
    """

    task: str = ""
    files_touched: deque[str] = field(default_factory=lambda: deque(maxlen=_MAX_FILES))
    notes: dict[str, str] = field(default_factory=dict)
    _note_order: deque[str] = field(default_factory=lambda: deque(maxlen=_MAX_NOTES))

    def touch_file(self, path: str) -> None:
        if not path:
            return
        if path in self.files_touched:
            self.files_touched.remove(path)
        self.files_touched.append(path)

    def save_note(self, key: str, content: str) -> None:
        key = key.strip()[:_NOTE_KEY_LEN]
        if not key:
            return
        if key in self.notes:
            self._note_order.remove(key)
        elif len(self._note_order) == _MAX_NOTES:
            evicted = self._note_order.popleft()
            self.notes.pop(evicted, None)
        self._note_order.append(key)
        self.notes[key] = content.strip()

    def query_notes(self, query: str = "") -> dict[str, str]:
        if not query:
            return dict(self.notes)
        q = query.lower()
        return {k: v for k, v in self.notes.items() if q in k.lower() or q in v.lower()}

    def render(self) -> str:
        files = ", ".join(self.files_touched) if self.files_touched else "-"
        if self.notes:
            notes = "\n".join(f"  - {k}: {v}" for k, v in self.notes.items())
        else:
            notes = "  - (none)"
        return f"task: {self.task or '-'}\nfiles_touched: {files}\nnotes:\n{notes}"


@dataclass
class HarnessDeps:
    settings: HarnessSettings
    workspace: WorkspaceContext
    session_store: UnifiedStore
    session_id: str
    policy: RuntimePolicy
    model_adapter: ModelAdapter
    memory_store: MemoryStore | None = None
    persona_meta: dict[str, Any] = field(default_factory=dict)
    delegation_depth: int = 0
    retrieved_memories: str = field(default_factory=str)
    working_memory: WorkingMemory = field(default_factory=WorkingMemory)
    background_runner: BackgroundRunner | None = None
