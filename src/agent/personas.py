from __future__ import annotations

from dataclasses import dataclass
from functools import cache, lru_cache
from pathlib import Path
from typing import Any

import frontmatter
from pydantic_ai import RunContext

from src.policy import HarnessDeps

_PROMPTS_DIR = Path(__file__).resolve().parents[1] / "prompts"
_PERSONAS_DIR = _PROMPTS_DIR / "agents"
_INSTRUCTIONS_DIR = _PROMPTS_DIR / "instructions"
_RULES_DIR = _PROMPTS_DIR / "rules"
_SKILLS_DIR = _PROMPTS_DIR / "skills"


@dataclass(frozen=True)
class PromptDocument:
    name: str
    description: str
    content: str
    metadata: dict[str, Any]


def _load_md(path: Path) -> PromptDocument:
    post = frontmatter.loads(path.read_text(encoding="utf-8"))
    meta = dict(post.metadata)
    return PromptDocument(
        name=meta.get("name", path.stem),
        description=meta.get("description", ""),
        content=post.content.strip(),
        metadata=meta,
    )


def _load_chain(name: str, seen: frozenset[str]) -> PromptDocument:
    if name in seen:
        raise ValueError(f"circular persona base chain detected: {' -> '.join([*seen, name])}")
    path = _PERSONAS_DIR / f"{name}.md"
    if not path.exists():
        raise FileNotFoundError(f"persona not found: {name} ({path})")
    doc = _load_md(path)
    base_name = doc.metadata.get("base")
    if not base_name:
        return doc
    base_doc = _load_chain(base_name, seen | {name})
    merged_content = f"{base_doc.content}\n\n{doc.content}"
    merged_meta = {**base_doc.metadata, **doc.metadata}
    merged_meta.pop("base", None)
    return PromptDocument(
        name=doc.name,
        description=doc.description,
        content=merged_content,
        metadata=merged_meta,
    )


@cache
def load_persona(name: str) -> PromptDocument:
    return _load_chain(name, frozenset())


def list_personas() -> list[PromptDocument]:
    out: list[PromptDocument] = []
    for path in sorted(_PERSONAS_DIR.glob("*.md")):
        if path.name.startswith("_"):
            continue
        out.append(load_persona(path.stem))
    return out


@lru_cache(maxsize=1)
def _dynamic_template() -> str:
    return _load_md(_INSTRUCTIONS_DIR / "_dynamic.md").content


def clear_persona_cache() -> None:
    """Invalidate persona, system-prompt, instruction, rule, skill, and
    dynamic-template caches.

    Called by the hot-reload watcher when any file under `src/prompts/`
    changes on disk.
    """
    load_persona.cache_clear()
    load_system_prompt.cache_clear()
    load_instructions.cache_clear()
    load_rule.cache_clear()
    list_rules.cache_clear()
    combined_rules_text.cache_clear()
    load_skill.cache_clear()
    list_skills.cache_clear()
    _dynamic_template.cache_clear()


def prompts_dir() -> Path:
    return _PROMPTS_DIR


@cache
def load_system_prompt(rel_path: str) -> str:
    """Load a raw-markdown prompt from `src/prompts/<rel_path>.md`.

    Helper for internal sub-agents whose instructions live alongside the
    user-facing personas. Accepts a slash-separated path like
    `agents/memory-extractor` or `instructions/memory-extract`. No
    frontmatter parsing.
    """
    path = _PROMPTS_DIR / f"{rel_path}.md"
    if not path.exists():
        raise FileNotFoundError(f"system prompt not found: {rel_path} ({path})")
    return path.read_text(encoding="utf-8").strip()


@cache
def load_instructions(name: str) -> str:
    """Load project-level instructions from `src/prompts/instructions/<name>.md`."""
    path = _INSTRUCTIONS_DIR / f"{name}.md"
    if not path.exists():
        raise FileNotFoundError(f"instructions file not found: {name} ({path})")
    return path.read_text(encoding="utf-8").strip()


@cache
def load_rule(name: str) -> str:
    """Load a single rule file from `src/prompts/rules/<name>.md`."""
    path = _RULES_DIR / f"{name}.md"
    if not path.exists():
        raise FileNotFoundError(f"rule not found: {name} ({path})")
    return path.read_text(encoding="utf-8").strip()


@cache
def list_rules() -> tuple[str, ...]:
    """Return the stems of all rule files under `src/prompts/rules/`."""
    if not _RULES_DIR.exists():
        return ()
    return tuple(sorted(p.stem for p in _RULES_DIR.glob("*.md")))


@cache
def combined_rules_text() -> str:
    """Concatenate every `rules/*.md` into one block for system-prompt injection."""
    chunks: list[str] = []
    for name in list_rules():
        chunks.append(f"## Rule: {name}\n\n{load_rule(name)}")
    return "\n\n".join(chunks)


@cache
def load_skill(name: str) -> str:
    """Load a skill's `SKILL.md` from `src/prompts/skills/<name>/SKILL.md`.

    Skills are on-demand knowledge modules, not loaded into the base
    system prompt. Agents pull them via a tool when a task matches.
    """
    path = _SKILLS_DIR / name / "SKILL.md"
    if not path.exists():
        raise FileNotFoundError(f"skill not found: {name} ({path})")
    return path.read_text(encoding="utf-8").strip()


@cache
def list_skills() -> tuple[str, ...]:
    """Return names of skills available under `src/prompts/skills/`."""
    if not _SKILLS_DIR.exists():
        return ()
    return tuple(
        sorted(p.name for p in _SKILLS_DIR.iterdir() if p.is_dir() and (p / "SKILL.md").exists())
    )


def render_dynamic(ctx: RunContext[HarnessDeps], persona_name: str) -> str:
    deps = ctx.deps
    mode = "read-only" if deps.settings.read_only else "read-write"
    recent_events = deps.session_store.describe_recent_events_sync(deps.session_id, limit=5)
    memories_context = getattr(deps, "retrieved_memories", "")
    open_todos = deps.session_store.todos.count_open_sync(deps.session_id)

    working_memory = deps.working_memory.render()
    if open_todos:
        working_memory += f"\nopen_todos: {open_todos} (use list_todos to inspect)"

    return _dynamic_template().format_map(
        {
            "session_id": deps.session_id,
            "persona_name": persona_name,
            "mode": mode,
            "approval_mode": deps.settings.approval_mode,
            "recent_events": recent_events or "No prior event summary available.",
            "memories": memories_context or "(none)",
            "working_memory": working_memory,
        }
    )


__all__ = [
    "PromptDocument",
    "clear_persona_cache",
    "combined_rules_text",
    "list_personas",
    "list_rules",
    "list_skills",
    "load_instructions",
    "load_persona",
    "load_rule",
    "load_skill",
    "load_system_prompt",
    "prompts_dir",
    "render_dynamic",
]
