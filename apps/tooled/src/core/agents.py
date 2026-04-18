from __future__ import annotations

import asyncio
import json
import os
import random
import time
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from enum import StrEnum
from typing import TYPE_CHECKING, Any, Generic, TypeVar

import httpx
from pydantic import BaseModel, TypeAdapter

from ._context import RunContext
from .hooks import ToolCall
from .memory import MemoryDecision, remember_impl
from .policy import load_policy
from .providers import Provider, get_provider
from .tool import dispatch_tool, registry_list, tools_schema
from .utils import logger

if TYPE_CHECKING:
    from .config import RuntimeConfig

ChatMessage = dict[str, Any]
ChatParams = dict[str, Any]

__all__ = [
    "Agent",
    "AgentConfig",
    "AgentError",
    "ChatMessage",
    "ChatParams",
    "ChatResponse",
    "ChatUsage",
    "CompactResult",
    "ModelRetry",
    "Reply",
    "RunContext",
    "Thinking",
    "run_memory_agent",
]

MAX_ATTEMPTS = 3
MODELS_CACHE_TTL = 60.0
SYSTEM_PROMPT = (
    "You are a helpful assistant that provides accurate and concise answers to the user. "
    "Always provide clear and relevant information based on the user's input. "
    "You should not make up information. If you don't know the answer, say you don't know. "
    "You should respond in the language of the user."
)
COMPACT_PROMPT = (
    "Summarize the conversation so far in <200 words. "
    "Preserve key decisions, facts, and context the assistant needs to continue."
)

T = TypeVar("T", bound=BaseModel)


def _format_history(messages: list[dict[str, Any]]) -> str:
    """Flatten messages into a plain-text transcript for the compact sub-agent."""
    lines: list[str] = []
    for m in messages:
        role = m.get("role", "?")
        content = m.get("content") or ""
        if role == "tool":
            lines.append(f"[tool:{m.get('name', '?')}] {content}")
        elif role == "assistant" and m.get("tool_calls"):
            names = ", ".join(tc.get("function", {}).get("name", "?") for tc in m["tool_calls"])
            lines.append(f"[assistant tool_calls={names}] {content}")
        else:
            lines.append(f"[{role}] {content}")
    return "\n".join(lines)


class Reply(BaseModel):
    """Default response model for plain-text chat."""

    content: str


class Thinking(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


def _clean_error_message(raw: str) -> str:
    if not isinstance(raw, str):
        return str(raw)
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return raw.strip()
    if isinstance(data, dict):
        err = data.get("error")
        if isinstance(err, dict) and isinstance(err.get("message"), str):
            return err["message"]
        if isinstance(err, str):
            return err
        if isinstance(data.get("message"), str):
            return data["message"]
    return raw.strip()


class AgentError(Exception):
    def __init__(self, message: str, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.clean_message = _clean_error_message(message)


class ModelRetry(Exception):
    """Raised when structured output fails validation; triggers a model retry."""


@asynccontextmanager
async def _http_errors() -> AsyncIterator[None]:
    try:
        yield
    except httpx.HTTPStatusError as e:
        try:
            text = e.response.text
        except httpx.ResponseNotRead:
            await e.response.aread()
            text = e.response.text
        raise AgentError(text, e.response.status_code) from e
    except httpx.HTTPError as e:
        raise AgentError(str(e)) from e


def _retryable(status_code: int | None) -> bool:
    if status_code is None:
        return False
    return status_code >= 500 or status_code == 429


def _retry_after_seconds(response: httpx.Response) -> float | None:
    raw = response.headers.get("retry-after")
    if not raw:
        return None
    try:
        return max(0.0, float(raw))
    except ValueError:
        return None


def _extract_reasoning(obj: dict[str, Any]) -> str:
    return obj.get("reasoning_content") or obj.get("reasoning") or ""


def _optional_float(raw: str | None) -> float | None:
    if raw is None or raw == "" or raw.lower() == "none":
        return None
    try:
        return float(raw)
    except ValueError:
        return None


@dataclass
class ChatUsage:
    prompt_tokens: int = 0
    completion_tokens: int = 0
    reasoning_tokens: int = 0
    total_tokens: int = 0

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> ChatUsage:
        data = data or {}
        details = data.get("completion_tokens_details") or {}
        return cls(
            prompt_tokens=data.get("prompt_tokens", 0) or 0,
            completion_tokens=data.get("completion_tokens", 0) or 0,
            reasoning_tokens=details.get("reasoning_tokens", 0) or 0,
            total_tokens=data.get("total_tokens", 0) or 0,
        )

    def to_dict(self) -> dict[str, int]:
        return {
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "reasoning_tokens": self.reasoning_tokens,
            "total_tokens": self.total_tokens,
        }

    def __iadd__(self, other: ChatUsage) -> ChatUsage:
        self.prompt_tokens += other.prompt_tokens
        self.completion_tokens += other.completion_tokens
        self.reasoning_tokens += other.reasoning_tokens
        self.total_tokens += other.total_tokens
        return self


@dataclass
class ChatResponse(Generic[T]):  # noqa: UP046
    message: dict[str, Any]
    model: str = "N/A"
    usage: ChatUsage = field(default_factory=ChatUsage)
    response_time: float = 0.0
    finish_reason: str | None = None
    reasoning: str = ""
    parsed: T | None = None

    @property
    def content(self) -> str:
        return self.message.get("content") or ""


@dataclass
class CompactResult:
    summarized: int
    kept: int
    tokens_used: int


@dataclass
class AgentConfig:
    provider: Provider
    model: str
    system_prompt: str = SYSTEM_PROMPT
    instructions: str = ""
    temperature: float | None = None
    max_tokens: int | None = None
    connect_timeout: float = field(default_factory=lambda: float(os.getenv("CONNECT_TIMEOUT", "10")))
    read_timeout: float = field(default_factory=lambda: float(os.getenv("READ_TIMEOUT", "60")))
    stream_read_timeout: float | None = field(default_factory=lambda: _optional_float(os.getenv("STREAM_READ_TIMEOUT")))
    max_output_retries: int = 2

    def __post_init__(self) -> None:
        if not self.provider.api_key:
            logger.warning(
                "Provider %r has empty api_key. Set %s env var if auth is required.",
                self.provider.name,
                "the configured" if not self.provider.api_key else "",
            )
        if self.instructions:
            self.instructions = self.instructions.strip()

    @classmethod
    def from_role(cls, runtime: RuntimeConfig, role_name: str) -> AgentConfig:
        role = runtime.role(role_name)
        prov = get_provider(role.provider)
        return cls(
            provider=prov,
            model=role.resolve_model(),
            instructions=role.instructions or "",
            temperature=role.temperature,
            max_tokens=role.max_tokens,
        )


class Agent(Generic[T]):  # noqa: UP046
    def __init__(
        self,
        config: AgentConfig,
        messages: list[dict[str, Any]] | None = None,
        response_model: type[T] = Reply,  # type: ignore[assignment]  # ty: ignore[invalid-parameter-default]
        max_tool_iterations: int = 10,
        policy: Any = None,
        use_tools: bool = True,
    ) -> None:
        self.config = config
        self.messages: list[dict[str, Any]] = list(messages) if messages else []
        self.response_model: type[T] = response_model
        self.max_tool_iterations = max_tool_iterations
        self._policy = policy
        self.use_tools = use_tools
        self.disabled_tools: set[str] = set()
        self.confirm_fn: Callable[[ToolCall], bool] | None = None
        self.toolset: Any = None  # Toolset | None -- set per-agent
        self.tool_filter: Callable[[Any, Any], bool] | None = None
        self.deps: Any = None
        self._local_hooks: list[tuple[str, Any, str | None]] = []  # (phase, fn, tool|None)
        self.runtime: RuntimeConfig | None = None
        self.session_usage = ChatUsage()
        self.turns = 0
        self._compact_snapshot: list[dict[str, Any]] | None = None
        self._models_cache: tuple[float, list[str]] | None = None
        self.client = self.config.provider.build_client(
            self.config.connect_timeout,
            self.config.read_timeout,
        )

    @property
    def system_prompt(self) -> list[dict[str, Any]]:
        prompt: list[dict[str, Any]] = [{"role": "system", "content": self.config.system_prompt}]
        if self.config.instructions:
            prompt.append({"role": "system", "content": self.config.instructions})
        return prompt

    @property
    def policy(self) -> Any:
        if self._policy is None:
            self._policy = load_policy()
        return self._policy

    def set_policy(self, policy: Any) -> None:
        self._policy = policy

    @property
    def local_hooks(self) -> list[tuple[str, Any, str | None]]:
        return list(self._local_hooks)

    def reset(self) -> None:
        self.messages = []
        self.session_usage = ChatUsage()
        self.turns = 0
        self._compact_snapshot = None

    def add_hook(self, phase: str, fn: Any, *, tool: str | None = None) -> None:
        self._local_hooks.append((phase, fn, tool))

    def config_for_role(self, role_name: str) -> AgentConfig:
        """Return AgentConfig for `role_name`, or own config if role/runtime absent."""
        if self.runtime is None or role_name not in self.runtime.roles:
            if self.runtime is not None:
                logger.debug("role %r not in runtime; falling back to own config", role_name)
            return self.config
        return AgentConfig.from_role(self.runtime, role_name)

    def scoped_disabled_tools(self, role_name: str) -> set[str]:
        """Tools to disable for a sub-agent running under `role_name` per runtime.tools scoping."""
        if self.runtime is None:
            return set()
        spec = self.runtime.tools.get(role_name)
        if spec is None:
            return set()
        disabled: set[str] = set(spec.deny)
        if spec.allow is not None:
            for entry in registry_list():
                name = entry["name"]
                if name and name not in spec.allow:
                    disabled.add(name)
        return disabled

    def _track(self, resp: ChatResponse[T]) -> ChatResponse[T]:
        self.session_usage += resp.usage
        self.turns += 1
        logger.info(
            "turn model=%s tokens=%s time=%.2fs finish=%s",
            resp.model,
            resp.usage.total_tokens,
            resp.response_time,
            resp.finish_reason,
        )
        return resp

    def pop_last_user(self) -> str | None:
        for i in range(len(self.messages) - 1, -1, -1):
            if self.messages[i].get("role") == "user":
                content = self.messages[i].get("content") or ""
                self.messages = self.messages[:i]
                return content
        return None

    async def models(self, force: bool = False) -> list[str]:
        now = time.monotonic()
        if not force and self._models_cache is not None:
            cached_at, cached = self._models_cache
            if now - cached_at < MODELS_CACHE_TTL:
                return cached
        async with _http_errors():
            r = await self.client.get("/models")
            r.raise_for_status()
        ids = sorted(m["id"] for m in r.json().get("data", []))
        self._models_cache = (now, ids)
        return ids

    async def compact(self, keep_last: int = 4) -> CompactResult | None:
        if len(self.messages) < keep_last + 4:
            return None
        snapshot = list(self.messages)
        to_keep = list(self.messages[-keep_last:])
        # Extend left to avoid splitting assistant(tool_calls) -> tool pairs
        while to_keep and to_keep[0].get("role") == "tool":
            pivot = len(self.messages) - len(to_keep) - 1
            if pivot >= 0:
                to_keep.insert(0, self.messages[pivot])
            else:
                break
        self.messages = self.messages[: len(self.messages) - len(to_keep)]
        summarized = len(self.messages)
        compact_cfg = self.config_for_role("compact")
        history_text = _format_history(snapshot)
        sub: Agent[Reply] = Agent(config=compact_cfg, use_tools=False)
        sub.runtime = self.runtime
        try:
            summary = await sub.chat(f"{COMPACT_PROMPT}\n\n{history_text}")
        except Exception:
            self.messages = snapshot
            await sub.aclose()
            raise
        finally:
            if not sub.client.is_closed:
                await sub.aclose()
        self.messages = [
            {"role": "system", "content": f"Prior context summary:\n{summary.content}"},
            *to_keep,
        ]
        self._compact_snapshot = snapshot
        return CompactResult(summarized=summarized, kept=keep_last, tokens_used=summary.usage.total_tokens)

    def undo_compact(self) -> int | None:
        if self._compact_snapshot is None:
            return None
        restored = len(self._compact_snapshot)
        self.messages = self._compact_snapshot
        self._compact_snapshot = None
        return restored

    async def _post_with_retry(self, path: str, payload: dict[str, Any]) -> httpx.Response:
        last_exc: AgentError | None = None
        for attempt in range(MAX_ATTEMPTS):
            try:
                response = await self.client.post(path, json=payload)
                response.raise_for_status()
                return response
            except httpx.HTTPStatusError as e:
                status = e.response.status_code
                last_exc = AgentError(e.response.text, status)
                if not _retryable(status) or attempt == MAX_ATTEMPTS - 1:
                    raise last_exc from e
                delay = _retry_after_seconds(e.response)
                if delay is None:
                    delay = 0.5 * (2**attempt) + random.random() * 0.1
                logger.warning("retry attempt=%d status=%d delay=%.2fs", attempt + 1, status, delay)
                await asyncio.sleep(delay)
            except httpx.TimeoutException as e:
                last_exc = AgentError(str(e))
                if attempt == MAX_ATTEMPTS - 1:
                    raise last_exc from e
                logger.warning("retry timeout attempt=%d", attempt + 1)
                await asyncio.sleep(0.5 * (2**attempt) + random.random() * 0.1)
            except httpx.HTTPError as e:
                raise AgentError(str(e)) from e
        assert last_exc is not None
        raise last_exc

    def _build_payload(self, params: dict[str, Any] | None = None, **kwargs: Any) -> dict[str, Any]:
        defaults: dict[str, Any] = {}
        if self.config.temperature is not None:
            defaults["temperature"] = self.config.temperature
        if self.config.max_tokens is not None:
            defaults["max_tokens"] = self.config.max_tokens
        payload: dict[str, Any] = {
            "model": self.config.model,
            "messages": self.system_prompt + self.messages,
            **defaults,
            **(params or {}),
            **kwargs,
        }
        if self.use_tools:
            raw_tools = self.toolset.schema() if self.toolset is not None else tools_schema()
            tools = [t for t in raw_tools if t["function"]["name"] not in self.disabled_tools]
            if self.tool_filter is not None:
                tools = [t for t in tools if self.tool_filter(t, self)]
            if tools:
                payload["tools"] = tools
                payload["tool_choice"] = "auto"
        if self.response_model is not Reply:
            adapter: TypeAdapter[T] = TypeAdapter(self.response_model)
            payload["response_format"] = {
                "type": "json_schema",
                "json_schema": {
                    "name": self.response_model.__name__,
                    "schema": adapter.json_schema(),
                    "strict": True,
                },
            }
        return payload

    def _parse_response(self, content: str) -> T:
        if self.response_model is Reply:
            return Reply(content=content)  # type: ignore[return-value]
        try:
            return TypeAdapter(self.response_model).validate_json(content)
        except Exception as exc:
            raise ModelRetry(f"Output failed validation as {self.response_model.__name__}: {exc}") from exc

    async def _dispatch_tool_calls(self, raw_calls: list[dict[str, Any]]) -> None:
        async def _run(call: ToolCall) -> str:
            if self.toolset is not None:
                return await self.toolset.dispatch(call)
            return await dispatch_tool(call, self._local_hooks or None)

        allow_calls: list[tuple[str, ToolCall]] = []
        confirm_calls: list[tuple[str, ToolCall]] = []

        for raw in raw_calls:
            fn = raw.get("function", {})
            call = ToolCall(
                id=raw["id"],
                name=fn.get("name", ""),
                args=json.loads(fn.get("arguments", "{}")),
            )
            verdict = self.policy.gate(call.name, call.args)
            if verdict == "deny":
                self.messages.append({
                    "role": "tool",
                    "tool_call_id": call.id,
                    "content": f"Error: tool {call.name!r} is denied by policy",
                })
                continue
            if verdict == "confirm":
                confirm_calls.append((raw["id"], call))
            else:
                allow_calls.append((raw["id"], call))

        if allow_calls:
            results = await asyncio.gather(*[_run(c) for _, c in allow_calls])
            for (tc_id, _), result in zip(allow_calls, results, strict=True):
                self.messages.append({"role": "tool", "tool_call_id": tc_id, "content": result})

        for tc_id, call in confirm_calls:
            approved = self.confirm_fn(call) if self.confirm_fn is not None else False
            if not approved:
                self.messages.append({
                    "role": "tool",
                    "tool_call_id": tc_id,
                    "content": f"Tool {call.name!r} was denied by the user",
                })
            else:
                result = await _run(call)
                self.messages.append({"role": "tool", "tool_call_id": tc_id, "content": result})

    async def chat(
        self,
        user_input: str,
        params: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> ChatResponse[T]:
        if user_input:
            self.messages.append({"role": "user", "content": user_input})
        payload = self._build_payload(params, **kwargs)

        logger.debug("POST /chat/completions model=%s messages=%d", self.config.model, len(self.messages))

        for iteration in range(self.max_tool_iterations):
            start = time.perf_counter()
            response = await self._post_with_retry("/chat/completions", payload)
            elapsed = time.perf_counter() - start

            data = response.json()
            choices = data.get("choices", [])
            if not choices:
                raise ValueError("No choices returned from the API.")

            choice = choices[0]
            message = choice.get("message", {})
            finish_reason: str | None = choice.get("finish_reason")
            self.messages.append(message)

            if finish_reason == "tool_calls":
                if iteration == self.max_tool_iterations - 1:
                    raise AgentError(f"Max tool iterations ({self.max_tool_iterations}) exceeded")
                raw_calls = message.get("tool_calls") or []
                await self._dispatch_tool_calls(raw_calls)
                payload = self._build_payload(params, **kwargs)
                continue

            parsed: T | None = None
            for retry in range(self.config.max_output_retries + 1):
                try:
                    parsed = self._parse_response(message.get("content") or "")
                    break
                except ModelRetry as e:
                    if retry >= self.config.max_output_retries:
                        logger.warning("output validation failed after %d retries: %s", retry + 1, e)
                        parsed = None
                        break
                    logger.info("output retry #%d: %s", retry + 1, e)
                    self.messages.append({"role": "user", "content": f"Your output was invalid. Fix and try again.\n\nError: {e}"})
                    payload = self._build_payload(params, **kwargs)
                    start = time.perf_counter()
                    response = await self._post_with_retry("/chat/completions", payload)
                    elapsed = time.perf_counter() - start
                    data = response.json()
                    choices = data.get("choices", [])
                    if not choices:
                        break
                    choice = choices[0]
                    message = choice.get("message", {})
                    finish_reason = choice.get("finish_reason")
                    self.messages.append(message)
                    if finish_reason == "tool_calls":
                        # model called tools during retry -- dispatch and keep looping
                        raw_calls = message.get("tool_calls") or []
                        await self._dispatch_tool_calls(raw_calls)
                        payload = self._build_payload(params, **kwargs)
                        break

            return self._track(
                ChatResponse(
                    message=message,
                    model=data.get("model", "N/A"),
                    usage=ChatUsage.from_dict(data.get("usage")),
                    response_time=round(elapsed, 2),
                    finish_reason=finish_reason,
                    reasoning=_extract_reasoning(message),
                    parsed=parsed,
                )
            )

        raise AgentError(f"Max tool iterations ({self.max_tool_iterations}) exceeded")

    def _stream_timeout(self) -> httpx.Timeout:
        return httpx.Timeout(
            connect=self.config.connect_timeout,
            read=self.config.stream_read_timeout,
            write=self.config.connect_timeout,
            pool=self.config.connect_timeout,
        )

    async def chat_stream(
        self,
        user_input: str,
        on_content: Callable[[str], None] | None = None,
        on_reasoning: Callable[[str], None] | None = None,
        params: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> ChatResponse[T]:
        self.messages.append({"role": "user", "content": user_input})
        payload = {
            **self._build_payload(params, **kwargs),
            "stream": True,
            "stream_options": {"include_usage": True},
        }

        logger.debug("stream POST /chat/completions model=%s messages=%d", self.config.model, len(self.messages))
        start = time.perf_counter()
        content_parts: list[str] = []
        reasoning_parts: list[str] = []
        tool_call_accum: dict[int, dict[str, Any]] = {}
        model_name = "N/A"
        usage_raw: dict[str, Any] = {}
        finish_reason: str | None = None

        async with _http_errors(), self.client.stream(
            "POST",
            "/chat/completions",
            json=payload,
            headers={"Accept": "text/event-stream"},
            timeout=self._stream_timeout(),
        ) as response:
            if response.status_code >= 400:
                await response.aread()
            response.raise_for_status()
            async for line in response.aiter_lines():
                if not line or not line.startswith("data: "):
                    continue
                data_str = line.removeprefix("data: ")
                if data_str == "[DONE]":
                    break
                chunk = json.loads(data_str)
                model_name = chunk.get("model", model_name)
                if chunk.get("usage"):
                    usage_raw = chunk["usage"]
                for choice in chunk.get("choices", []):
                    finish_reason = choice.get("finish_reason", finish_reason)
                    delta = choice.get("delta", {})
                    reason_piece = _extract_reasoning(delta)
                    if reason_piece:
                        reasoning_parts.append(reason_piece)
                        if on_reasoning:
                            on_reasoning(reason_piece)
                    piece = delta.get("content")
                    if piece:
                        content_parts.append(piece)
                        if on_content:
                            on_content(piece)
                    for tc in delta.get("tool_calls") or []:
                        idx = tc["index"]
                        if idx not in tool_call_accum:
                            tool_call_accum[idx] = {
                                "id": tc.get("id", ""),
                                "type": "function",
                                "function": {"name": "", "arguments": ""},
                            }
                        acc = tool_call_accum[idx]
                        fn = tc.get("function", {})
                        if tc.get("id"):
                            acc["id"] = tc["id"]
                        acc["function"]["name"] += fn.get("name") or ""
                        acc["function"]["arguments"] += fn.get("arguments") or ""

        elapsed = time.perf_counter() - start

        if finish_reason == "tool_calls":
            raw_calls = [tool_call_accum[i] for i in sorted(tool_call_accum)]
            tool_msg: dict[str, Any] = {
                "role": "assistant",
                "content": "".join(content_parts) or None,
                "tool_calls": raw_calls,
            }
            self.messages.append(tool_msg)
            await self._dispatch_tool_calls(raw_calls)
            # continue via non-streaming for remaining tool iterations
            return await self.chat("", params=params, **kwargs)

        message: dict[str, Any] = {"role": "assistant", "content": "".join(content_parts)}
        self.messages.append(message)
        parsed: T | None = None
        for retry in range(self.config.max_output_retries + 1):
            try:
                parsed = self._parse_response(message.get("content") or "")
                break
            except ModelRetry as e:
                if retry >= self.config.max_output_retries:
                    logger.warning("output validation failed after %d retries: %s", retry + 1, e)
                    break
                logger.info("output retry #%d: %s", retry + 1, e)
                self.messages.append({"role": "user", "content": f"Your output was invalid. Fix and try again.\n\nError: {e}"})
                return await self.chat("", params=params, **kwargs)
        return self._track(
            ChatResponse(
                message=message,
                model=model_name,
                usage=ChatUsage.from_dict(usage_raw),
                response_time=round(elapsed, 2),
                finish_reason=finish_reason,
                reasoning="".join(reasoning_parts),
                parsed=parsed,
            )
        )

    async def aclose(self) -> None:
        await self.client.aclose()

    async def __aenter__(self) -> Agent[T]:
        return self

    async def __aexit__(self, exc_type: object, exc: object, tb: object) -> None:
        await self.aclose()


_MEMORY_PROMPT = (
    "You are a memory curator. Given the conversation turn below, decide if anything "
    "should be saved to memory.\n\n"
    "- medium: facts, preferences, observations that may be useful across sessions\n"
    "- long: stable definitions, rules, invariants worth preserving indefinitely\n\n"
    "If nothing is worth saving, set save=false.\n\nConversation turn:\n{turn}"
)


async def run_memory_agent(turn_text: str, agent: Agent[Any]) -> None:
    mem_cfg = agent.config_for_role("memory")
    mem_agent: Agent[MemoryDecision] = Agent(
        config=mem_cfg,
        response_model=MemoryDecision,
        use_tools=False,
    )
    mem_agent.runtime = agent.runtime
    try:
        decision = await mem_agent.chat(_MEMORY_PROMPT.format(turn=turn_text))
        parsed = decision.parsed
        if parsed is None or not parsed.save or not parsed.content:
            return
        remember_impl(parsed.content, parsed.tags, parsed.tier or "medium")
        logger.debug("memory agent saved to %s: %s", parsed.tier, parsed.content[:60])
    except Exception:
        logger.debug("memory agent failed (non-fatal)", exc_info=True)
    finally:
        await mem_agent.aclose()
