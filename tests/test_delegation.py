from __future__ import annotations

from src.agent import AgentBuilder
from src.agent.delegation import build_delegation_tools
from src.model import HarnessSettings, ModelAdapter
from src.session import UnifiedStore


def _builder(settings: HarnessSettings, store: UnifiedStore) -> AgentBuilder:
    return AgentBuilder(settings, ModelAdapter(settings), store)


def test_build_delegation_tools_empty_for_non_delegating_persona(
    harness_settings: HarnessSettings, session_store: UnifiedStore
):
    builder = _builder(harness_settings, session_store)
    tools = build_delegation_tools(builder, {"delegates": []})
    assert tools == []


def test_build_delegation_tools_emits_tool_per_target(
    harness_settings: HarnessSettings, session_store: UnifiedStore
):
    builder = _builder(harness_settings, session_store)
    tools = build_delegation_tools(builder, {"delegates": ["coder", "reviewer"]})
    names = {t.name for t in tools}
    assert names == {"delegate_to_coder", "delegate_to_reviewer"}
    for tool in tools:
        assert tool.metadata == {
            "category": "delegation",
            "target": tool.name.removeprefix("delegate_to_"),
        }


def test_delegation_tool_docstring_mentions_structured_return(
    harness_settings: HarnessSettings, session_store: UnifiedStore
):
    builder = _builder(harness_settings, session_store)
    tools = build_delegation_tools(builder, {"delegates": ["coder"]})
    doc = tools[0].function.__doc__ or ""
    assert "persona" in doc
    assert "status" in doc
    assert "answer" in doc
