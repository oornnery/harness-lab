from __future__ import annotations

from pathlib import Path
from typing import Literal

from dotenv import load_dotenv
from pydantic import Field
from pydantic_ai import ModelSettings, UsageLimits
from pydantic_ai.models import Model
from pydantic_ai.models.fallback import FallbackModel
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.providers.openai import OpenAIProvider
from pydantic_settings import BaseSettings, SettingsConfigDict

load_dotenv()

ApprovalMode = Literal["manual", "auto-safe", "never"]


class HarnessSettings(BaseSettings):
    """Runtime settings for the educational harness.

    The agent model itself is intentionally provider-agnostic. PydanticAI accepts a
    model string like ``openai:gpt-5.2`` or any other compatible provider/model pair.
    """

    model_config = SettingsConfigDict(
        env_prefix="HARNESS_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    model: str = Field(
        default="openai:gpt-5.2",
        validation_alias="MODEL",
    )
    base_url: str | None = Field(default=None, validation_alias="OPENAI_BASE_URL")
    api_key: str | None = Field(default=None, validation_alias="OPENAI_API_KEY")
    workspace: Path = Field(default_factory=Path.cwd)
    session_dir: Path = Path(".harness")
    read_only: bool = False
    approval_mode: ApprovalMode = "manual"
    temperature: float = 0.0
    max_tokens: int = 4_000
    max_history_messages: int = 24
    tool_timeout_seconds: int = 30
    shell_timeout_seconds: int = 300
    max_search_hits: int = 50
    max_file_lines: int = 250
    show_thinking: bool = False
    include_git_context: bool = True
    usage_limits: UsageLimits | None = Field(default=None, exclude=True)
    fallback_models: list[str] = Field(default_factory=list, validation_alias="FALLBACK_MODELS")
    summarize_model: str | None = Field(default=None, validation_alias="SUMMARIZE_MODEL")
    summarize_keep_last: int = 10
    adaptive_trim_threshold: int = 80_000
    adaptive_trim_floor: int = 6
    mcp_config_path: Path | None = Field(default=None, validation_alias="MCP_CONFIG_PATH")
    logfire_enable: bool = False
    logfire_capture_http: bool = False

    def resolved_workspace(self) -> Path:
        return self.workspace.expanduser().resolve()

    def resolved_session_dir(self) -> Path:
        session_dir = self.session_dir.expanduser()
        if not session_dir.is_absolute():
            session_dir = self.resolved_workspace() / session_dir
        return session_dir.resolve()


class ModelAdapter:
    """Thin adapter around PydanticAI model selection and settings.

    In a more productized harness this layer would also be the place to add:

    - provider failover
    - budget/cost routing
    - capability-aware provider choices
    - per-task model overrides
    """

    def __init__(self, settings: HarnessSettings) -> None:
        self.settings = settings

    @property
    def model_name(self) -> str:
        return self.settings.model

    def supports_native_output(self) -> bool:
        name = self.settings.model.lower()
        if name.startswith("anthropic:"):
            return True
        if name.startswith("google"):
            return True
        if name.startswith("openai:"):
            raw = name.split(":", 1)[1]
            return raw.startswith(("gpt-4o", "gpt-4.1", "gpt-5", "o1", "o3", "o4"))
        return False

    def _build_primary(self) -> Model | str:
        model = self.settings.model
        if self.settings.base_url or self.settings.api_key:
            raw = model.split(":", 1)[1] if model.startswith("openai:") else model
            provider = OpenAIProvider(
                base_url=self.settings.base_url,
                api_key=self.settings.api_key,
            )
            return OpenAIChatModel(raw, provider=provider)
        return model

    def build_model(self) -> Model | str:
        primary = self._build_primary()
        if not self.settings.fallback_models:
            return primary
        return FallbackModel(primary, *self.settings.fallback_models)

    def build_model_settings(self) -> ModelSettings:
        return ModelSettings(
            temperature=self.settings.temperature,
            max_tokens=self.settings.max_tokens,
        )

    def explain(self) -> str:
        mode = "read-only" if self.settings.read_only else "read-write"
        return (
            f"model={self.model_name} | workspace={self.settings.resolved_workspace()} | "
            f"mode={mode} | approval={self.settings.approval_mode}"
        )
