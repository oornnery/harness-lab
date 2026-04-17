from __future__ import annotations

import json
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from .utils import logger

__all__ = ["DEFAULT_POLICY", "Policy", "ToolDenied", "load_policy", "save_policy"]

POLICY_FILE = Path.cwd() / ".tooled" / "policy.json"


class ToolDenied(Exception):
    def __init__(self, name: str) -> None:
        super().__init__(f"Tool denied by policy: {name!r}")
        self.tool_name = name


class Policy(BaseModel):
    allow: set[str] = Field(default_factory=set)
    confirm: set[str] = Field(default_factory=set)
    deny: set[str] = Field(default_factory=set)

    model_config = ConfigDict(frozen=True)

    def gate(self, name: str) -> Literal["allow", "confirm", "deny"]:
        if name in self.deny:
            return "deny"
        if name in self.confirm:
            return "confirm"
        if name in self.allow:
            return "allow"
        return "confirm"  # unknown tools require confirmation by default

    def with_verdict(self, name: str, verdict: Literal["allow", "confirm", "deny"]) -> Policy:
        allow = set(self.allow)
        confirm = set(self.confirm)
        deny = set(self.deny)
        for s in (allow, confirm, deny):
            s.discard(name)
        if verdict == "allow":
            allow.add(name)
        elif verdict == "confirm":
            confirm.add(name)
        else:
            deny.add(name)
        return Policy(allow=allow, confirm=confirm, deny=deny)


# Catalog defaults -- tools safe to run without prompting
_DEFAULT_ALLOW = {"read_file", "list_dir", "grep", "web_search", "remember", "recall"}
_DEFAULT_CONFIRM = {"write_file", "shell", "fetch"}
DEFAULT_POLICY = Policy(allow=_DEFAULT_ALLOW, confirm=_DEFAULT_CONFIRM)


def load_policy() -> Policy:
    if not POLICY_FILE.exists():
        return DEFAULT_POLICY
    try:
        data = json.loads(POLICY_FILE.read_text())
        return Policy.model_validate(data)
    except Exception:
        logger.warning("Failed to load policy from %s, using default", POLICY_FILE)
        return Policy()


def save_policy(policy: Policy) -> None:
    POLICY_FILE.parent.mkdir(parents=True, exist_ok=True)
    POLICY_FILE.write_text(policy.model_dump_json(indent=2))
