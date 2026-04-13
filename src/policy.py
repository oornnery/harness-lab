from __future__ import annotations

import hashlib
import json
from collections import OrderedDict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from pydantic_ai import (
    ModelRetry,
)

from .context import IGNORED_DIRS, WorkspaceContext
from .model import HarnessSettings
from .sessions import SessionStore

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


@dataclass
class HarnessDeps:
    settings: HarnessSettings
    workspace: WorkspaceContext
    session_store: SessionStore
    session_id: str
    policy: RuntimePolicy
    persona_meta: dict[str, Any] = field(default_factory=dict)
    delegation_depth: int = 0
