from __future__ import annotations

import asyncio
import functools
import inspect
from dataclasses import dataclass, field
from typing import Any

from pydantic import TypeAdapter, create_model

from ..hooks import ToolCall, run_post_hooks, run_pre_hooks
from ..utils import logger

__all__ = ["dispatch_tool", "registry_list", "tool", "tools_schema"]


@dataclass
class ToolEntry:
    name: str
    desc: str
    fn: Any  # async callable
    args_adapter: Any  # TypeAdapter[ArgsModel]
    schema: dict[str, Any]
    timeout: float | None = field(default=None)


_REGISTRY: dict[str, ToolEntry] = {}


def tool(
    name: str | None = None,
    *,
    desc: str = "",
    timeout: float | None = None,
) -> Any:
    """Register a function as a tool available to the model.

    @tool(name="read_file", desc="Read a text file")
    async def read_file(path: str) -> str: ...
    """
    def deco(fn: Any) -> Any:
        tool_name = name or fn.__name__

        sig = inspect.signature(fn)
        fields: dict[str, Any] = {}
        for pname, param in sig.parameters.items():
            ann = param.annotation if param.annotation is not inspect.Parameter.empty else Any
            default = ... if param.default is inspect.Parameter.empty else param.default
            fields[pname] = (ann, default)

        ArgsModel = create_model(f"{tool_name}_args", **fields)
        adapter: TypeAdapter[Any] = TypeAdapter(ArgsModel)
        raw_schema = adapter.json_schema()
        # strip pydantic title from parameters object
        raw_schema.pop("title", None)

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
        )
        return wrapper

    return deco


def tools_schema() -> list[dict[str, Any]]:
    return [e.schema for e in _REGISTRY.values()]


def registry_list() -> list[dict[str, str | None]]:
    return [{"name": e.name, "desc": e.desc} for e in _REGISTRY.values()]


async def dispatch_tool(call: ToolCall) -> str:
    entry = _REGISTRY.get(call.name)
    if entry is None:
        return f"Error: unknown tool {call.name!r}"

    try:
        validated = entry.args_adapter.validate_python(call.args)
    except Exception as exc:
        return f"Error: invalid args for {call.name!r}: {exc}"

    run_pre_hooks(call)

    try:
        coro = entry.fn(**validated.model_dump())
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
    output = run_post_hooks(call, output)
    return output


# import catalog modules so their @tool decorators register on import
from . import agent_tool, fs, shell, web  # noqa: E402, F401
