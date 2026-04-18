from __future__ import annotations

import asyncio
import functools
import inspect
from dataclasses import dataclass, field
from typing import Any, get_type_hints

from pydantic import BaseModel, TypeAdapter, create_model

from ._context import RunContext, _run_ctx
from .hooks import ToolCall, run_post_hooks, run_pre_hooks
from .utils import logger

__all__ = [
    "ToolEntry",
    "Toolset",
    "dispatch_tool",
    "registry_list",
    "tool",
    "tools_schema",
]


@dataclass
class ToolEntry:
    name: str
    desc: str
    fn: Any  # async callable
    args_adapter: Any  # TypeAdapter[ArgsModel]
    schema: dict[str, Any]
    timeout: float | None = field(default=None)
    has_ctx: bool = field(default=False)
    returns_adapter: Any = field(default=None)  # TypeAdapter[ReturnsModel] | None


def _parse_docstring_args(docstring: str | None) -> dict[str, str]:
    """Extract param descriptions from Google-style ``Args:`` section."""
    if not docstring:
        return {}
    lines = docstring.splitlines()
    in_args = False
    result: dict[str, str] = {}
    current_name: str | None = None
    current_desc: list[str] = []
    for line in lines:
        stripped = line.strip()
        if stripped == "Args:":
            in_args = True
            continue
        if in_args:
            if stripped and not stripped[0].isspace() and stripped.endswith(":") and not current_name:
                break
            if not stripped:
                if current_name:
                    result[current_name] = " ".join(current_desc).strip()
                    current_name = None
                    current_desc = []
                continue
            if current_name and line.startswith("        "):
                current_desc.append(stripped)
            elif ":" in stripped and not line.startswith("        "):
                if current_name:
                    result[current_name] = " ".join(current_desc).strip()
                parts = stripped.split(":", 1)
                current_name = parts[0].strip()
                current_desc = [parts[1].strip()] if len(parts) > 1 else []
    if current_name:
        result[current_name] = " ".join(current_desc).strip()
    return result


_REGISTRY: dict[str, ToolEntry] = {}


async def _dispatch_impl(
    entry: ToolEntry,
    call: ToolCall,
    local_hooks: list[tuple[str, Any, str | None]] | None = None,
) -> str:
    try:
        validated = entry.args_adapter.validate_python(call.args)
    except Exception as exc:
        return f"Error: invalid args for {call.name!r}: {exc}"

    agent_ref = _run_ctx.get(None)
    token = None
    if agent_ref is not None:
        ctx = RunContext(
            agent=agent_ref.agent,
            deps=agent_ref.agent.deps,
            tool_call=call,
            turn=agent_ref.agent.turns,
        )
        token = _run_ctx.set(ctx)

    try:
        hooks = local_hooks or (agent_ref.agent.local_hooks if agent_ref is not None else None)
        await run_pre_hooks(call, hooks)

        try:
            kwargs = {**validated.model_dump(), "ctx": _run_ctx.get(None)} if entry.has_ctx else validated.model_dump()
            coro = entry.fn(**kwargs)
            if entry.timeout is not None:
                result = await asyncio.wait_for(coro, entry.timeout)
            else:
                result = await coro
        except TimeoutError:
            logger.warning("tool %s timed out after %ss", call.name, entry.timeout)
            return f"Error: tool {call.name!r} timed out after {entry.timeout}s"
        except Exception as exc:
            logger.exception("tool %s raised", call.name)
            return f"Error: {exc}"

        output = str(result)
        if entry.returns_adapter is not None:
            try:
                output = str(entry.returns_adapter.validate_python(result))
            except Exception as exc:
                logger.warning("tool %s output validation failed: %s", call.name, exc)
                output = f"Error: tool output invalid: {exc}"
        output = await run_post_hooks(call, output, hooks)
        return output
    finally:
        if token is not None:
            _run_ctx.reset(token)


@dataclass
class Toolset:
    """Isolated tool registry. Falls back to global `_REGISTRY` for missing names."""

    tools: dict[str, ToolEntry] = field(default_factory=dict)

    def schema(self, disabled: set[str] | None = None) -> list[dict[str, Any]]:
        disabled = disabled or set()
        entries = self.tools or _REGISTRY
        return [e.schema for e in entries.values() if e.name not in disabled]

    async def dispatch(self, call: ToolCall) -> str:
        entry = self.tools.get(call.name) or _REGISTRY.get(call.name)
        if entry is None:
            return f"Error: unknown tool {call.name!r}"
        return await _dispatch_impl(entry, call)


def _is_run_context(annotation: Any) -> bool:
    """Check if annotation is RunContext or RunContext[SomeType]."""
    origin = getattr(annotation, "__origin__", None)
    if origin is not None:
        return origin is RunContext
    return annotation is RunContext


def tool(
    name: str | None = None,
    *,
    desc: str = "",
    timeout: float | None = None,
    returns: type[BaseModel] | None = None,
) -> Any:
    """Register a function as a tool available to the model.

    @tool(name="read_file", desc="Read a text file")
    async def read_file(path: str) -> str: ...

    Tools may declare a `ctx: RunContext[T]` first param -- it is injected
    at dispatch and excluded from the JSON schema.
    """
    def deco(fn: Any) -> Any:
        tool_name = name or fn.__name__

        sig = inspect.signature(fn)
        hints = get_type_hints(fn) if hasattr(fn, "__annotations__") else {}
        fields: dict[str, Any] = {}
        has_ctx = False
        params_iter = iter(sig.parameters.items())
        first_name, first_param = next(params_iter, (None, None))
        if first_name is not None and first_param is not None and _is_run_context(hints.get(first_name, first_param.annotation)):
            has_ctx = True
        elif first_name is not None and first_param is not None:
            ann = first_param.annotation if first_param.annotation is not inspect.Parameter.empty else Any
            default = ... if first_param.default is inspect.Parameter.empty else first_param.default
            fields[first_name] = (ann, default)

        for pname, param in params_iter:
            ann = param.annotation if param.annotation is not inspect.Parameter.empty else Any
            default = ... if param.default is inspect.Parameter.empty else param.default
            fields[pname] = (ann, default)

        ArgsModel = create_model(f"{tool_name}_args", **fields)
        adapter: TypeAdapter[Any] = TypeAdapter(ArgsModel)
        raw_schema = adapter.json_schema()
        raw_schema.pop("title", None)
        doc_args = _parse_docstring_args(fn.__doc__)
        if doc_args and "properties" in raw_schema:
            for pname, pdesc in doc_args.items():
                if pname in raw_schema["properties"]:
                    raw_schema["properties"][pname].setdefault("description", pdesc)

        schema = {
            "type": "function",
            "function": {
                "name": tool_name,
                "description": desc or (fn.__doc__ or "").strip(),
                "parameters": raw_schema,
            },
        }

        @functools.wraps(fn)
        async def wrapper(**kwargs: Any) -> Any:
            if has_ctx:
                kwargs["ctx"] = _run_ctx.get(None)
            if asyncio.iscoroutinefunction(fn):
                return await fn(**kwargs)
            return await asyncio.to_thread(fn, **kwargs)

        _REGISTRY[tool_name] = ToolEntry(
            name=tool_name,
            desc=desc or (fn.__doc__ or "").strip(),
            fn=wrapper,
            args_adapter=adapter,
            schema=schema,
            timeout=timeout,
            has_ctx=has_ctx,
            returns_adapter=TypeAdapter(returns) if returns is not None else None,
        )
        return wrapper

    return deco


def tools_schema() -> list[dict[str, Any]]:
    return [e.schema for e in _REGISTRY.values()]


def registry_list() -> list[dict[str, str | None]]:
    return [{"name": e.name, "desc": e.desc} for e in _REGISTRY.values()]


async def dispatch_tool(call: ToolCall, local_hooks: list[tuple[str, Any, str | None]] | None = None) -> str:
    entry = _REGISTRY.get(call.name)
    if entry is None:
        return f"Error: unknown tool {call.name!r}"
    return await _dispatch_impl(entry, call, local_hooks)
