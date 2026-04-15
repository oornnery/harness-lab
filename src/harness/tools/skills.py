"""Skill tools: on-demand domain knowledge modules from `src/prompts/skills/`."""

from __future__ import annotations

from pydantic_ai import RunContext

from src.agent.personas import list_skills as _list_skills
from src.agent.personas import load_skill as _load_skill
from src.policy import HarnessDeps


class SkillTools:
    async def list_skills(self, ctx: RunContext[HarnessDeps]) -> list[str]:
        """Return the names of every available skill under `src/prompts/skills/`."""
        return list(_list_skills())

    async def load_skill(self, ctx: RunContext[HarnessDeps], name: str) -> str:
        """Load a skill's full `SKILL.md` by name. Call only when the task matches.

        Args:
            name: Skill directory name (as returned by `list_skills`).
        """
        try:
            return _load_skill(name)
        except FileNotFoundError as exc:
            return f"[skill not found: {name}] available: {', '.join(_list_skills())}\n{exc}"
