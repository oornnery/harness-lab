"""
Credits:
- [Identity](https://www.youtube.com/watch?v=LykXu60aKoY)
"""

import json
import os
import random
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, TypedDict

import httpx

from .utils import logger

MAX_ATTEMPTS = 3
MODELS_CACHE_TTL = 60.0
SYSTEM_PROMPT = (
    "You are a helpful assistant that provides accurate and concise answers to user."
    "Always provide clear and relevant information based on the user's input."
    "You should not make up information. If you don't know the answer, say you don't know."
    "You should respond in the language of the user."
)
COMPACT_PROMPT = "Summarize the conversation so far in <200 words. Preserve key decisions, facts, and context the assistant needs to continue."


class ChatMessage(TypedDict, total=False):
    role: str
    content: str


class Thinking(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class ChatParams(TypedDict, total=False):
    temperature: float
    top_p: float
    max_tokens: int
    seed: int
    reasoning_effort: Thinking


def _clean_error_message(raw: str) -> str:
    """Pull `error.message` out of provider JSON; fallback to trimmed raw."""
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
    def __init__(self, message: str, status_code: int | None = None):
        super().__init__(message)
        self.status_code = status_code
        self.clean_message = _clean_error_message(message)


@contextmanager
def _http_errors() -> Iterator[None]:
    try:
        yield
    except httpx.HTTPStatusError as e:
        try:
            text = e.response.text
        except httpx.ResponseNotRead:
            e.response.read()
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
    """Providers expose reasoning under either `reasoning_content` or `reasoning`."""
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
    def from_dict(cls, data: dict[str, Any] | None) -> "ChatUsage":
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

    def __iadd__(self, other: "ChatUsage") -> "ChatUsage":
        self.prompt_tokens += other.prompt_tokens
        self.completion_tokens += other.completion_tokens
        self.reasoning_tokens += other.reasoning_tokens
        self.total_tokens += other.total_tokens
        return self


@dataclass
class ChatResponse:
    message: ChatMessage
    model: str = "N/A"
    usage: ChatUsage = field(default_factory=ChatUsage)
    response_time: float = 0.0
    finish_reason: str | None = None
    reasoning: str = ""

    @property
    def content(self) -> str:
        return self.message.get("content", "")


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
    # None disables the read timeout for streaming (SSE can idle between tokens).
    stream_read_timeout: float | None = field(default_factory=lambda: _optional_float(os.getenv("STREAM_READ_TIMEOUT")))

    def __post_init__(self) -> None:
        if not self.api_key:
            raise ValueError("API key is required. Please set the API_KEY environment variable.")
        self.base_url = self.base_url.rstrip("/")
        if self.instructions:
            self.instructions = self.instructions.strip()


class Agent:
    def __init__(self, config: AgentConfig | None = None, messages: list[ChatMessage] | None = None):
        self.config = config or AgentConfig()
        self.messages: list[ChatMessage] = list(messages) if messages else []
        self.session_usage = ChatUsage()
        self.turns = 0
        self._compact_snapshot: list[ChatMessage] | None = None
        self._models_cache: tuple[float, list[str]] | None = None
        self.client = httpx.Client(
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
    def system_prompt(self) -> list[ChatMessage]:
        prompt: list[ChatMessage] = [{"role": "system", "content": self.config.system_prompt}]
        if self.config.instructions:
            prompt.append({"role": "system", "content": self.config.instructions})
        return prompt

    def reset(self) -> None:
        self.messages = []
        self.session_usage = ChatUsage()
        self.turns = 0
        self._compact_snapshot = None

    def _track(self, resp: ChatResponse) -> ChatResponse:
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
        """Drop the last user message and anything after it; return its content."""
        for i in range(len(self.messages) - 1, -1, -1):
            if self.messages[i].get("role") == "user":
                content = self.messages[i].get("content") or ""
                self.messages = self.messages[:i]
                return content
        return None

    def models(self, force: bool = False) -> list[str]:
        now = time.monotonic()
        if not force and self._models_cache is not None:
            cached_at, cached = self._models_cache
            if now - cached_at < MODELS_CACHE_TTL:
                return cached
        with _http_errors():
            r = self.client.get("/models")
            r.raise_for_status()
        ids = sorted(m["id"] for m in r.json().get("data", []))
        self._models_cache = (now, ids)
        return ids

    def compact(self, keep_last: int = 4) -> CompactResult | None:
        if len(self.messages) < keep_last + 4:
            return None
        snapshot = list(self.messages)
        to_keep = self.messages[-keep_last:]
        self.messages = self.messages[:-keep_last]
        summarized = len(self.messages)
        try:
            summary = self.chat(COMPACT_PROMPT, params={"temperature": 0.3})
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

    def _post_with_retry(self, path: str, payload: dict[str, Any]) -> httpx.Response:
        last_exc: AgentError | None = None
        for attempt in range(MAX_ATTEMPTS):
            try:
                response = self.client.post(path, json=payload)
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
                time.sleep(delay)
                continue
            except httpx.TimeoutException as e:
                last_exc = AgentError(str(e))
                if attempt == MAX_ATTEMPTS - 1:
                    raise last_exc from e
                logger.warning("retry timeout attempt=%d", attempt + 1)
                time.sleep(0.5 * (2**attempt) + random.random() * 0.1)
                continue
            except httpx.HTTPError as e:
                raise AgentError(str(e)) from e
        assert last_exc is not None
        raise last_exc

    def chat(self, user_input: str, params: ChatParams | None = None, **kwargs: Any) -> ChatResponse:
        self.messages.append({"role": "user", "content": user_input})
        payload = {
            "model": self.config.model,
            "messages": self.system_prompt + self.messages,
            **(params or {}),
            **kwargs,
        }

        logger.debug("POST /chat/completions model=%s messages=%d", self.config.model, len(self.messages))
        start = time.perf_counter()
        response = self._post_with_retry("/chat/completions", payload)
        elapsed = time.perf_counter() - start

        data = response.json()
        choices = data.get("choices", [])
        if not choices:
            raise ValueError("No choices returned from the API.")

        choice = choices[0]
        message = choice.get("message", {})
        if not message:
            raise ValueError("No message returned in the choice.")

        self.messages.append(message)
        return self._track(
            ChatResponse(
                message=message,
                model=data.get("model", "N/A"),
                usage=ChatUsage.from_dict(data.get("usage")),
                response_time=round(elapsed, 2),
                finish_reason=choice.get("finish_reason"),
                reasoning=_extract_reasoning(message),
            )
        )

    def _stream_timeout(self) -> httpx.Timeout:
        return httpx.Timeout(
            connect=self.config.connect_timeout,
            read=self.config.stream_read_timeout,
            write=self.config.connect_timeout,
            pool=self.config.connect_timeout,
        )

    def chat_stream(
        self,
        user_input: str,
        on_content: Callable[[str], None] | None = None,
        on_reasoning: Callable[[str], None] | None = None,
        params: ChatParams | None = None,
        **kwargs: Any,
    ) -> ChatResponse:
        self.messages.append({"role": "user", "content": user_input})
        payload = {
            "model": self.config.model,
            "messages": self.system_prompt + self.messages,
            "stream": True,
            "stream_options": {"include_usage": True},
            **(params or {}),
            **kwargs,
        }

        logger.debug("stream POST /chat/completions model=%s messages=%d", self.config.model, len(self.messages))
        start = time.perf_counter()
        content_parts: list[str] = []
        reasoning_parts: list[str] = []
        model_name = "N/A"
        usage_raw: dict[str, Any] = {}
        finish_reason: str | None = None

        with (
            _http_errors(),
            self.client.stream(
                "POST",
                "/chat/completions",
                json=payload,
                headers={"Accept": "text/event-stream"},
                timeout=self._stream_timeout(),
            ) as response,
        ):
            if response.status_code >= 400:
                response.read()
            response.raise_for_status()
            for line in response.iter_lines():
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

        elapsed = time.perf_counter() - start
        message: ChatMessage = {"role": "assistant", "content": "".join(content_parts)}
        self.messages.append(message)
        return self._track(
            ChatResponse(
                message=message,
                model=model_name,
                usage=ChatUsage.from_dict(usage_raw),
                response_time=round(elapsed, 2),
                finish_reason=finish_reason,
                reasoning="".join(reasoning_parts),
            )
        )

    def close(self) -> None:
        self.client.close()

    def __enter__(self) -> "Agent":
        return self

    def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
        self.close()
