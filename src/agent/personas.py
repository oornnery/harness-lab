from __future__ import annotations

from dataclasses import dataclass
from functools import cache, lru_cache
from pathlib import Path
from typing import Any

import frontmatter
from pydantic_ai import RunContext

from ..policy import HarnessDeps

_PROMPTS_DIR = Path(__file__).parent.parent / "prompts"


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
    path = _PROMPTS_DIR / f"{name}.md"
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
    for path in sorted(_PROMPTS_DIR.glob("*.md")):
        if path.name.startswith("_"):
            continue
        out.append(load_persona(path.stem))
    return out


@lru_cache(maxsize=1)
def _dynamic_template() -> str:
    return _load_md(_PROMPTS_DIR / "_dynamic.md").content


def render_dynamic(ctx: RunContext[HarnessDeps], persona_name: str) -> str:
    deps = ctx.deps
    mode = "read-only" if deps.settings.read_only else "read-write"
    recent_events = deps.session_store.describe_recent_events_sync(deps.session_id, limit=5)
    return _dynamic_template().format_map(
        {
            "session_id": deps.session_id,
            "persona_name": persona_name,
            "mode": mode,
            "approval_mode": deps.settings.approval_mode,
            "workspace_summary": deps.workspace.prompt_summary(),
            "recent_events": recent_events or "No prior event summary available.",
        }
    )


__all__ = ["PromptDocument", "list_personas", "load_persona", "render_dynamic"]
