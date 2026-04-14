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
    max_steps_per_turn: int = 12
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
    mcp_tool_prefix: str = "mcp_"
    logfire_enable: bool = False
    logfire_capture_http: bool = False
    hot_reload_personas: bool = False

    # Memory system
    enable_memory: bool = Field(default=True, validation_alias="ENABLE_MEMORY")
    memory_db_path: Path = Field(
        default=Path(".harness/memory.db"), validation_alias="MEMORY_DB_PATH"
    )
    memory_extraction_threshold: float = 0.7
    max_memories_per_session: int = 100
    memory_embedding_model: str = "sentence-transformers/all-MiniLM-L6-v2"

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
        self._supports_native_output = self._check_native_output()

    def _check_native_output(self) -> bool:
        """Check if the configured model supports native structured output."""
        provider, raw = self._parse_model_name()
        if provider == "anthropic":
            return True
        if provider == "google":
            return True
        if provider == "openai" and raw:
            return raw.startswith(("gpt-4o", "gpt-4.1", "gpt-5", "o1", "o3", "o4"))
        return False

    @property
    def model_name(self) -> str:
        return self.settings.model

    def _parse_model_name(self) -> tuple[str, str | None]:
        """Parse model string into (provider, model_name) tuple.

        Returns:
            (provider, raw_model) where provider is 'openai', 'anthropic', 'google', etc.
            and raw_model is the model name without prefix (or None if no prefix).
        """
        model = self.settings.model
        if ":" in model:
            provider, raw = model.split(":", 1)
            return provider.lower(), raw
        return "openai", model

    def supports_native_output(self) -> bool:
        """Check if the configured model supports native structured output.

        Result is cached at initialization.
        """
        return self._supports_native_output

    def _build_primary(self) -> Model | str:
        _, raw = self._parse_model_name()
        if self.settings.base_url or self.settings.api_key:
            model_name = raw if raw else self.settings.model
            provider_obj = OpenAIProvider(
                base_url=self.settings.base_url,
                api_key=self.settings.api_key,
            )
            return OpenAIChatModel(model_name, provider=provider_obj)
        return self.settings.model

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
