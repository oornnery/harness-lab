from __future__ import annotations

import asyncio
import json
import os
import random
import time
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Generic, TypeVar

import httpx
from pydantic import BaseModel, TypeAdapter

from .hooks import ToolCall
from .utils import logger

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
    "Reply",
    "Thinking",
]

# Set to current Agent before each tool dispatch, reset after; lets tools access parent agent.
_agent_ctx: ContextVar[Any] = ContextVar("agent_ctx")

MAX_ATTEMPTS = 3
MODELS_CACHE_TTL = 60.0
SYSTEM_PROMPT = (
    "You are a helpful assistant that provides accurate and concise answers to user."
    "Always provide clear and relevant information based on the user's input."
    "You should not make up information. If you don't know the answer, say you don't know."
    "You should respond in the language of the user."
)
COMPACT_PROMPT = (
    "Summarize the conversation so far in <200 words. "
    "Preserve key decisions, facts, and context the assistant needs to continue."
)

T = TypeVar("T", bound=BaseModel)


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
    model: str = field(default_factory=lambda: os.getenv("MODEL", "gpt-4"))
    base_url: str = field(default_factory=lambda: os.getenv("BASE_URL", "https://api.openai.com/v1"))
    api_key: str = field(default_factory=lambda: os.getenv("API_KEY", ""))
    system_prompt: str = SYSTEM_PROMPT
    instructions: str = ""
    connect_timeout: float = field(default_factory=lambda: float(os.getenv("CONNECT_TIMEOUT", "10")))
    read_timeout: float = field(default_factory=lambda: float(os.getenv("READ_TIMEOUT", "60")))
    stream_read_timeout: float | None = field(default_factory=lambda: _optional_float(os.getenv("STREAM_READ_TIMEOUT")))

    def __post_init__(self) -> None:
        if not self.api_key:
            raise ValueError("API key is required. Please set the API_KEY environment variable.")
        self.base_url = self.base_url.rstrip("/")
        if self.instructions:
            self.instructions = self.instructions.strip()


class Agent(Generic[T]):  # noqa: UP046
    def __init__(
        self,
        config: AgentConfig | None = None,
        messages: list[dict[str, Any]] | None = None,
        response_model: type[T] = Reply,  # type: ignore[assignment]  # ty: ignore[invalid-parameter-default]
        max_tool_iterations: int = 10,
        policy: Any = None,
        use_tools: bool = True,
    ) -> None:
        self.config = config or AgentConfig()
        self.messages: list[dict[str, Any]] = list(messages) if messages else []
        self.response_model: type[T] = response_model
        self.max_tool_iterations = max_tool_iterations
        self._policy = policy
        self.use_tools = use_tools
        self.disabled_tools: set[str] = set()
        self.confirm_fn: Callable[[ToolCall], bool] | None = None
        self.session_usage = ChatUsage()
        self.turns = 0
        self._compact_snapshot: list[dict[str, Any]] | None = None
        self._models_cache: tuple[float, list[str]] | None = None
        self.client = httpx.AsyncClient(
            base_url=self.config.base_url,
            headers={
                "Authorization": f"Bearer {self.config.api_key}",
                "Content-Type": "application/json",
            },
            timeout=httpx.Timeout(
                connect=self.config.connect_timeout,
                read=self.config.read_timeout,
                write=self.config.connect_timeout,
                pool=self.config.connect_timeout,
            ),
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
            from .policy import load_policy
            self._policy = load_policy()
        return self._policy

    def reset(self) -> None:
        self.messages = []
        self.session_usage = ChatUsage()
        self.turns = 0
        self._compact_snapshot = None

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
        try:
            summary = await self.chat(COMPACT_PROMPT, params={"temperature": 0.3})
        except Exception:
            self.messages = snapshot
            raise
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
        from .tools import tools_schema
        payload: dict[str, Any] = {
            "model": self.config.model,
            "messages": self.system_prompt + self.messages,
            **(params or {}),
            **kwargs,
        }
        if self.use_tools:
            tools = [
                t for t in tools_schema()
                if t["function"]["name"] not in self.disabled_tools
            ]
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
        except Exception:
            logger.warning("failed to parse response as %s", self.response_model.__name__)
            return None  # type: ignore[return-value]  # ty: ignore[invalid-return-type]

    async def _dispatch_tool_calls(self, raw_calls: list[dict[str, Any]]) -> None:
        # lazy import: tools.agent_tool imports agent, so top-level import would be circular
        from .tools import dispatch_tool

        allow_calls: list[tuple[str, ToolCall]] = []
        confirm_calls: list[tuple[str, ToolCall]] = []

        for raw in raw_calls:
            fn = raw.get("function", {})
            call = ToolCall(
                id=raw["id"],
                name=fn.get("name", ""),
                args=json.loads(fn.get("arguments", "{}")),
            )
            verdict = self.policy.gate(call.name)
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

        token = _agent_ctx.set(self)
        try:
            if allow_calls:
                results = await asyncio.gather(*[dispatch_tool(c) for _, c in allow_calls])
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
                    result = await dispatch_tool(call)
                    self.messages.append({"role": "tool", "tool_call_id": tc_id, "content": result})
        finally:
            _agent_ctx.reset(token)

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

            parsed = self._parse_response(message.get("content") or "")
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
        parsed = self._parse_response(message.get("content") or "")
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
