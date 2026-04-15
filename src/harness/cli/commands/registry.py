"""Built-in command registry."""

from __future__ import annotations

from .agent import agent_command, mode_command, step_command
from .base import CommandSpec, HarnessExtension
from .jobs import jobs_command
from .logs import logs_command
from .memory import forget_command, memory_command
from .misc import attach_command, context_command, help_command, tools_command
from .schedule import schedule_command
from .session import (
    clear_command,
    compact_command,
    fork_command,
    replay_command,
    resume_command,
    session_command,
)
from .todos import todos_command

_DEFAULT_EXTENSIONS: list[HarnessExtension] | None = None


def default_extensions() -> list[HarnessExtension]:
    global _DEFAULT_EXTENSIONS
    if _DEFAULT_EXTENSIONS is not None:
        return _DEFAULT_EXTENSIONS
    inspector = HarnessExtension(
        name="inspector",
        description="PI-style CLI inspection commands.",
        commands=[
            CommandSpec("help", "Show slash commands.", help_command),
            CommandSpec("context", "Show the current workspace context.", context_command),
            CommandSpec("tools", "List known tools.", tools_command),
            CommandSpec("session", "Show current session info and recent events.", session_command),
            CommandSpec(
                "fork", "Fork the current conversation into a child session.", fork_command
            ),
            CommandSpec("replay", "Replay recent structured session events.", replay_command),
            CommandSpec(
                "clear", "Clear conversation history for the current session.", clear_command
            ),
            CommandSpec(
                "compact",
                "Compact history, keeping last N messages (default: half of max).",
                compact_command,
            ),
            CommandSpec(
                "resume",
                "Resume a previous session. No arg lists available sessions.",
                resume_command,
            ),
            CommandSpec(
                "agent",
                "Switch persona. No arg lists available personas.",
                agent_command,
            ),
            CommandSpec(
                "mode",
                "Show or toggle mode (readonly|manual|auto|never|budget=...).",
                mode_command,
            ),
            CommandSpec(
                "attach",
                "Attach a file or URL to the next turn.",
                attach_command,
            ),
            CommandSpec(
                "step",
                "Run a prompt step-by-step via agent.iter (debug).",
                step_command,
            ),
            CommandSpec(
                "memory",
                "Show and manage persistent memories across sessions.",
                memory_command,
            ),
            CommandSpec(
                "forget",
                "Delete a specific memory by ID.",
                forget_command,
            ),
            CommandSpec(
                "todos",
                "List/manage session todos. /todos add <title> | done|doing|rm <id>.",
                todos_command,
            ),
            CommandSpec(
                "jobs",
                "Monitor background agent jobs. /jobs show <id> | /jobs cancel <id>.",
                jobs_command,
            ),
            CommandSpec(
                "schedule",
                "Manage scheduled tasks. /schedule add <when> :: <prompt>.",
                schedule_command,
            ),
            CommandSpec(
                "logs",
                "Filtered session events. /logs [kind=K] [tool=N] [since=SEC] [last=N].",
                logs_command,
            ),
        ],
    )
    _DEFAULT_EXTENSIONS = [inspector]
    return _DEFAULT_EXTENSIONS


def build_command_index() -> dict[str, CommandSpec]:
    index: dict[str, CommandSpec] = {}
    for extension in default_extensions():
        for command in extension.commands:
            if command.name in index:
                raise ValueError(f"Duplicate command name detected: {command.name}")
            index[command.name] = command
    return index
