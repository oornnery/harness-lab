from __future__ import annotations

import time
from collections.abc import AsyncIterable
from typing import Any

from pydantic_ai import (
    AgentStreamEvent,
    FunctionToolCallEvent,
    FunctionToolResultEvent,
    PartStartEvent,
    RunContext,
    TextPart,
    ToolCallPart,
    ToolDefinition,
)
from pydantic_ai.capabilities.hooks import Hooks

from src.memory.extractor import get_extractor
from src.policy import HarnessDeps


def build_harness_hooks() -> Hooks[HarnessDeps]:
    """Build the harness `Hooks` capability.

    Streams tool-call, tool-result and text events into the `SessionStore`
    and also records per-tool elapsed time + model request errors.
    """

    hooks = Hooks[HarnessDeps]()

    @hooks.on.run_event_stream
    async def _on_run_event_stream(
        ctx: RunContext[HarnessDeps],
        *,
        stream: AsyncIterable[AgentStreamEvent],
    ) -> AsyncIterable[AgentStreamEvent]:
        async for event in stream:
            if isinstance(event, FunctionToolCallEvent):
                await ctx.deps.session_store.append_event(
                    ctx.deps.session_id,
                    {
                        "kind": "tool-call",
                        "tool": event.part.tool_name,
                        "args": event.part.args,
                        "tool_call_id": event.part.tool_call_id,
                    },
                )
            elif isinstance(event, FunctionToolResultEvent):
                await ctx.deps.session_store.append_event(
                    ctx.deps.session_id,
                    {
                        "kind": "tool-result",
                        "tool_call_id": event.tool_call_id,
                        "result": repr(event.result.content)[:500],
                    },
                )
            elif isinstance(event, PartStartEvent) and isinstance(event.part, TextPart):
                await ctx.deps.session_store.append_event(
                    ctx.deps.session_id,
                    {
                        "kind": "text-start",
                        "content": event.part.content[:200],
                    },
                )
            yield event

    @hooks.on.before_tool_execute
    async def _on_before_tool(
        ctx: RunContext[HarnessDeps],
        *,
        call: ToolCallPart,
        tool_def: ToolDefinition,
        args: Any,
    ) -> Any:
        ctx.deps.policy.tool_timings[call.tool_call_id] = time.monotonic()
        return args

    @hooks.on.after_tool_execute
    async def _on_after_tool(
        ctx: RunContext[HarnessDeps],
        *,
        call: ToolCallPart,
        tool_def: ToolDefinition,
        args: Any,
        result: Any,
    ) -> Any:
        start = ctx.deps.policy.tool_timings.pop(call.tool_call_id, None)
        if start is not None:
            elapsed_ms = int((time.monotonic() - start) * 1000)
            await ctx.deps.session_store.append_event(
                ctx.deps.session_id,
                {
                    "kind": "tool-timing",
                    "tool": tool_def.name,
                    "tool_call_id": call.tool_call_id,
                    "elapsed_ms": elapsed_ms,
                },
            )
        return result

    @hooks.on.tool_execute_error
    async def _on_tool_error(
        ctx: RunContext[HarnessDeps],
        *,
        call: ToolCallPart,
        tool_def: ToolDefinition,
        args: Any,
        error: Exception,
    ) -> Any:
        ctx.deps.policy.tool_timings.pop(call.tool_call_id, None)
        await ctx.deps.session_store.append_event(
            ctx.deps.session_id,
            {
                "kind": "tool-execute-error",
                "tool": tool_def.name,
                "tool_call_id": call.tool_call_id,
                "error_type": type(error).__name__,
                "message": str(error)[:500],
            },
        )
        raise error

    @hooks.on.prepare_tools
    async def _on_prepare_tools(
        ctx: RunContext[HarnessDeps],
        tool_defs: list[ToolDefinition],
    ) -> list[ToolDefinition]:
        persona_readonly = ctx.deps.persona_meta.get("default_mode") == "read-only"
        global_readonly = ctx.deps.settings.read_only
        if not (persona_readonly or global_readonly):
            return tool_defs
        mutating = {"mutate", "shell"}
        return [td for td in tool_defs if (td.metadata or {}).get("category") not in mutating]

    @hooks.on.before_model_request
    async def _on_before_model_request(
        ctx: RunContext[HarnessDeps],
        request_context: Any,
        /,
    ) -> Any:
        messages = getattr(request_context, "messages", None) or []

        # Fetch relevant memories if enabled
        if ctx.deps.settings.enable_memory and ctx.deps.memory_store and messages:
            try:
                # Get user's query from the first ModelRequest's UserPromptPart
                last_user_msg = ""
                first_msg = messages[0]

                if hasattr(first_msg, "parts") and first_msg.parts:
                    first_part = first_msg.parts[0]
                    if hasattr(first_part, "content"):
                        last_user_msg = str(first_part.content).strip()

                if last_user_msg:
                    # Search for relevant memories
                    memories = await ctx.deps.memory_store.search_memories(
                        last_user_msg,
                        limit=3,
                    )

                    if memories:
                        memory_lines = []
                        for m in memories:
                            memory_lines.append(f"- [{m.entity_type}] {m.content}")
                        # Store in deps for render_dynamic to use
                        ctx.deps.retrieved_memories = "\n".join(memory_lines)

                        # Log for debugging
                        await ctx.deps.session_store.append_event(
                            ctx.deps.session_id,
                            {
                                "kind": "memory-injection",
                                "query": last_user_msg,
                                "memories_count": len(memories),
                            },
                        )
                    else:
                        ctx.deps.retrieved_memories = ""
            except Exception as e:
                # Don't fail the request if memory injection fails
                await ctx.deps.session_store.append_event(
                    ctx.deps.session_id,
                    {
                        "kind": "memory-injection-error",
                        "error_type": type(e).__name__,
                        "message": str(e)[:500],
                    },
                )
        else:
            ctx.deps.retrieved_memories = ""

        await ctx.deps.session_store.append_event(
            ctx.deps.session_id,
            {
                "kind": "model-request-start",
                "message_count": len(messages),
                "total_tokens_so_far": getattr(getattr(ctx, "usage", None), "total_tokens", 0) or 0,
            },
        )
        return request_context

    @hooks.on.after_model_request
    async def _on_after_model_request(
        ctx: RunContext[HarnessDeps],
        *,
        request_context: Any,
        response: Any,
    ) -> Any:
        """Extract memories after model response."""
        usage = getattr(response, "usage", None)
        await ctx.deps.session_store.append_event(
            ctx.deps.session_id,
            {
                "kind": "model-request-end",
                "input_tokens": getattr(usage, "input_tokens", None),
                "output_tokens": getattr(usage, "output_tokens", None),
            },
        )

        # Extract memories if enabled
        if not ctx.deps.settings.enable_memory:
            return response

        if ctx.deps.memory_store is None:
            return response

        # Extract messages from conversation
        messages_text = []
        for msg in ctx.messages:
            if hasattr(msg, "content"):
                messages_text.append(msg.content)
            elif hasattr(msg, "parts"):
                for part in msg.parts:
                    if hasattr(part, "content"):
                        messages_text.append(part.content)

        if not messages_text:
            return response

        # Use memory extraction agent
        try:
            extraction_model = ctx.deps.model_adapter.build_model()
            extractor = get_extractor(extraction_model)
            memories = await extractor.extract(
                session_id=ctx.deps.session_id,
                messages=messages_text,
                max_memories=10,
            )

            # Filter by confidence threshold
            threshold = ctx.deps.settings.memory_extraction_threshold
            filtered = [m for m in memories if m.confidence >= threshold]

            if filtered:
                await ctx.deps.memory_store.save_memories(filtered)
                await ctx.deps.session_store.append_event(
                    ctx.deps.session_id,
                    {
                        "kind": "memory-extracted",
                        "count": len(filtered),
                        "memories": [m.content for m in filtered],
                    },
                )
        except Exception as e:
            # Don't fail the request if memory extraction fails
            await ctx.deps.session_store.append_event(
                ctx.deps.session_id,
                {
                    "kind": "memory-extraction-error",
                    "error_type": type(e).__name__,
                    "message": str(e)[:500],
                },
            )

        return response

    @hooks.on.model_request_error
    async def _on_model_request_error(
        ctx: RunContext[HarnessDeps],
        *,
        request_context: Any,
        error: Exception,
    ) -> Any:
        await ctx.deps.session_store.append_event(
            ctx.deps.session_id,
            {
                "kind": "model-request-error",
                "error_type": type(error).__name__,
                "message": str(error)[:500],
            },
        )
        raise error

    return hooks
