"""Tool registration for pydantic-ai agents."""

from __future__ import annotations

from pydantic_ai import RunContext, Tool
from src.policy import HarnessDeps

from .background import BackgroundTools
from .file import FileTools
from .notes import NotesTools
from .policy import PolicyGuard
from .scheduling import SchedulingTools
from .search import SearchTools
from .shell import ShellTools
from .skills import SkillTools
from .todos import TodoTools

TOOL_NAMES = [
    "list_files",
    "read_file",
    "search_text",
    "write_file",
    "replace_in_file",
    "run_shell",
    "save_note",
    "query_notes",
    "add_todo",
    "list_todos",
    "update_todo",
    "delete_todo",
    "spawn_background",
    "list_background_jobs",
    "get_job_status",
    "get_job_result",
    "cancel_job",
    "schedule_task",
    "list_scheduled",
    "pause_scheduled",
    "resume_scheduled",
    "cancel_scheduled",
    "list_skills",
    "load_skill",
]


class ToolRegistry:
    """Register and provide tools to agents."""

    def __init__(self, deps: HarnessDeps) -> None:
        self.deps = deps
        self.policy_guard = PolicyGuard(deps)
        self.files = FileTools(self.policy_guard, deps)
        self.search = SearchTools(self.policy_guard, deps)
        self.shell = ShellTools(self.policy_guard, deps)
        self.notes = NotesTools()
        self.todos = TodoTools()
        self.background = BackgroundTools()
        self.scheduling = SchedulingTools()
        self.skills = SkillTools()

    def as_tools(self) -> list[Tool[HarnessDeps]]:
        return [
            Tool(self.files.list_files, metadata={"category": "read"}),
            Tool(self.files.read_file, metadata={"category": "read"}),
            Tool(self.search.search_text, metadata={"category": "read"}),
            Tool(self.files.write_file, metadata={"category": "mutate"}),
            Tool(self.files.replace_in_file, metadata={"category": "mutate"}),
            Tool(
                self.shell.execute,
                metadata={"category": "shell"},
                requires_approval=True,
                timeout=float(self.deps.settings.shell_timeout_seconds),
            ),
            Tool(self.notes.save_note, metadata={"category": "memory"}),
            Tool(self.notes.query_notes, metadata={"category": "memory"}),
            Tool(self.todos.add_todo, metadata={"category": "todo"}),
            Tool(self.todos.list_todos, metadata={"category": "todo"}),
            Tool(self.todos.update_todo, metadata={"category": "todo"}),
            Tool(self.todos.delete_todo, metadata={"category": "todo"}),
            Tool(self.background.spawn_background, metadata={"category": "background"}),
            Tool(self.background.list_background_jobs, metadata={"category": "background"}),
            Tool(self.background.get_job_status, metadata={"category": "background"}),
            Tool(self.background.get_job_result, metadata={"category": "background"}),
            Tool(self.background.cancel_job, metadata={"category": "background"}),
            Tool(self.scheduling.schedule_task, metadata={"category": "scheduling"}),
            Tool(self.scheduling.list_scheduled, metadata={"category": "scheduling"}),
            Tool(self.scheduling.pause_scheduled, metadata={"category": "scheduling"}),
            Tool(self.scheduling.resume_scheduled, metadata={"category": "scheduling"}),
            Tool(self.scheduling.cancel_scheduled, metadata={"category": "scheduling"}),
            Tool(self.skills.list_skills, metadata={"category": "read"}),
            Tool(self.skills.load_skill, metadata={"category": "read"}),
        ]


class ToolRuntime(ToolRegistry):
    """Runtime facade exposing every tool as a direct method."""

    async def list_files(
        self, ctx: RunContext[HarnessDeps], path: str = ".", limit: int = 200
    ) -> list[str]:
        return await self.files.list_files(ctx, path, limit)

    async def read_file(
        self,
        ctx: RunContext[HarnessDeps],
        path: str,
        start_line: int = 1,
        end_line: int | None = None,
    ) -> str:
        return await self.files.read_file(ctx, path, start_line, end_line)

    async def search_text(
        self,
        ctx: RunContext[HarnessDeps],
        query: str,
        path: str = ".",
        limit: int | None = None,
    ) -> list[str]:
        return await self.search.search_text(ctx, query, path, limit)

    async def write_file(self, ctx: RunContext[HarnessDeps], path: str, content: str) -> str:
        return await self.files.write_file(ctx, path, content)

    async def replace_in_file(
        self,
        ctx: RunContext[HarnessDeps],
        path: str,
        old: str,
        new: str,
        expected_replacements: int = 1,
    ) -> str:
        return await self.files.replace_in_file(ctx, path, old, new, expected_replacements)

    async def run_shell(
        self,
        ctx: RunContext[HarnessDeps],
        command: str,
        timeout: int | None = None,
    ) -> str:
        return await self.shell.execute(ctx, command, timeout)

    async def list_skills(self, ctx: RunContext[HarnessDeps]) -> list[str]:
        return await self.skills.list_skills(ctx)

    async def load_skill(self, ctx: RunContext[HarnessDeps], name: str) -> str:
        return await self.skills.load_skill(ctx, name)
