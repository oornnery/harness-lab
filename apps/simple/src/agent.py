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

MAX_ATTEMPTS = 3
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


class AgentError(Exception):
    def __init__(self, message: str, status_code: int | None = None):
        super().__init__(message)
        self.status_code = status_code


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
    return status_code is not None and status_code >= 500


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
    system_prompt: str = (
        "You are a helpful assistant that provides accurate and concise answers to user."
        "Always provide clear and relevant information based on the user's input."
        "You should not make up information. If you don't know the answer, say you don't know."
        "You should respond in the language of the user."
    )
    instructions: str = ""
    timeout: float = 30

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
        self.client = httpx.Client(
            base_url=self.config.base_url,
            headers={
                "Authorization": f"Bearer {self.config.api_key}",
                "Content-Type": "application/json",
            },
            timeout=self.config.timeout,
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

    def _track(self, resp: ChatResponse) -> ChatResponse:
        self.session_usage += resp.usage
        self.turns += 1
        return resp

    def models(self) -> list[str]:
        with _http_errors():
            r = self.client.get("/models")
            r.raise_for_status()
        return sorted(m["id"] for m in r.json().get("data", []))

    def compact(self, keep_last: int = 4) -> CompactResult | None:
        if len(self.messages) < keep_last + 4:
            return None
        to_keep = self.messages[-keep_last:]
        self.messages = self.messages[:-keep_last]
        summarized = len(self.messages)
        summary = self.chat(COMPACT_PROMPT, params={"temperature": 0.3})
        self.messages = [
            {"role": "system", "content": f"Prior context summary:\n{summary.content}"},
            *to_keep,
        ]
        return CompactResult(
            summarized=summarized,
            kept=keep_last,
            tokens_used=summary.usage.total_tokens,
        )

    def _post_with_retry(self, path: str, payload: dict[str, Any]) -> httpx.Response:
        last_exc: AgentError | None = None
        for attempt in range(MAX_ATTEMPTS):
            try:
                response = self.client.post(path, json=payload)
                response.raise_for_status()
                return response
            except httpx.HTTPStatusError as e:
                last_exc = AgentError(e.response.text, e.response.status_code)
                if not _retryable(e.response.status_code) or attempt == MAX_ATTEMPTS - 1:
                    raise last_exc from e
            except httpx.TimeoutException as e:
                last_exc = AgentError(str(e))
                if attempt == MAX_ATTEMPTS - 1:
                    raise last_exc from e
            except httpx.HTTPError as e:
                raise AgentError(str(e)) from e
            time.sleep(0.5 * (2**attempt) + random.random() * 0.1)
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
                reasoning=message.get("reasoning_content") or "",
            )
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
                    reason_piece = delta.get("reasoning_content") or delta.get("reasoning")
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
