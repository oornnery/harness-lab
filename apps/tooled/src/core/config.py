from __future__ import annotations

import os
import tomllib
from pathlib import Path

from pydantic import BaseModel, Field, model_validator

from .policy import DEFAULT_POLICY, Policy
from .providers import OpenAICompatProvider, register_provider
from .utils import logger

__all__ = [
    "CONFIG_FILE",
    "ModelSpec",
    "ProviderSpec",
    "RoleSpec",
    "RuntimeConfig",
    "ToolRoleSpec",
    "load_runtime_config",
]

CONFIG_FILE = Path.cwd() / ".tooled" / "config.toml"


class ModelSpec(BaseModel):
    name: str
    temperature: float | None = None
    max_tokens: int | None = None
    thinking: str | None = None  # low | medium | high


class ProviderSpec(BaseModel):
    """Declares a remote chat endpoint.

    Either `base_url` (literal) or `base_url_env` (env var name) must
    resolve to a non-empty URL. `api_key_env` always holds the env var
    holding the auth token.
    """

    base_url: str | None = None
    base_url_env: str | None = None
    api_key_env: str
    headers: dict[str, str] = Field(default_factory=dict)
    models: list[ModelSpec] = Field(default_factory=list)
    default_model: str | None = None

    @model_validator(mode="after")
    def _has_url_source(self) -> ProviderSpec:
        if not self.base_url and not self.base_url_env:
            raise ValueError("provider must set base_url or base_url_env")
        return self

    def resolve_base_url(self) -> str:
        if self.base_url:
            return self.base_url
        if self.base_url_env:
            val = os.getenv(self.base_url_env, "")
            if val:
                return val
            raise ValueError(
                f"base_url_env {self.base_url_env!r} is not set in environment"
            )
        raise ValueError("no base_url source configured")

    def find_model(self, name: str) -> ModelSpec | None:
        return next((m for m in self.models if m.name == name), None)


class RoleSpec(BaseModel):
    """Binds a semantic role (main, compact, ...) to a provider + model.

    Either `model` or `model_env` must resolve to a non-empty model id.
    """

    provider: str
    model: str | None = None
    model_env: str | None = None
    temperature: float | None = None
    max_tokens: int | None = None
    instructions: str | None = None

    @model_validator(mode="after")
    def _has_model_source(self) -> RoleSpec:
        if not self.model and not self.model_env:
            raise ValueError("role must set model or model_env")
        return self

    def resolve_model(self) -> str:
        if self.model:
            return self.model
        if self.model_env:
            val = os.getenv(self.model_env, "")
            if val:
                return val
            raise ValueError(f"model_env {self.model_env!r} is not set in environment")
        raise ValueError("no model source configured")


class ToolRoleSpec(BaseModel):
    allow: list[str] | None = None
    deny: list[str] = Field(default_factory=list)


class RuntimeConfig(BaseModel):
    default_role: str = "main"
    providers: dict[str, ProviderSpec]
    roles: dict[str, RoleSpec]
    tools: dict[str, ToolRoleSpec] = Field(default_factory=dict)
    policy: Policy = Field(default_factory=lambda: DEFAULT_POLICY)

    def register_providers(self) -> None:
        for name, spec in self.providers.items():
            api_key = os.getenv(spec.api_key_env, "")
            if not api_key:
                logger.warning("provider %s: env %s is empty", name, spec.api_key_env)
            register_provider(
                OpenAICompatProvider(
                    name=name,
                    base_url=spec.resolve_base_url(),
                    api_key=api_key,
                    extra_headers=spec.headers,
                )
            )

    def role(self, name: str) -> RoleSpec:
        if name not in self.roles:
            raise KeyError(f"Unknown role {name!r}. Available: {list(self.roles)}")
        return self.roles[name]


def _discover_env_providers() -> list[tuple[str, str]]:
    """Return [(provider_name_lower, PREFIX)] for every `{PREFIX}_API_KEY`
    env var found, ignoring the generic legacy `API_KEY`.
    """
    found: list[tuple[str, str]] = []
    for key in sorted(os.environ):
        if key == "API_KEY" or not key.endswith("_API_KEY"):
            continue
        prefix = key.removesuffix("_API_KEY")
        if not prefix:
            continue
        found.append((prefix.lower(), prefix))
    return found


def _render_default_config() -> str:
    """Generate default config.toml text from discovered env providers.

    Falls back to a legacy single-provider template referencing the
    generic `API_KEY`/`BASE_URL`/`MODEL` env vars when no prefixed
    providers are present.
    """
    discovered = _discover_env_providers()
    if not discovered:
        return _LEGACY_TEMPLATE
    lines = ['default_role = "main"', ""]
    for name, prefix in discovered:
        lines.append(f"[providers.{name}]")
        lines.append(f'base_url_env = "{prefix}_BASE_URL"')
        lines.append(f'api_key_env  = "{prefix}_API_KEY"')
        lines.append("")
    main_name, main_prefix = discovered[0]
    lines.append("[roles.main]")
    lines.append(f'provider  = "{main_name}"')
    lines.append(f'model_env = "{main_prefix}_MODEL"')
    lines.append("")
    # Sub-agent roles default to the main provider; edit to route them
    # across providers (e.g. compact on a cheaper small model).
    for role in ("compact", "memory", "delegate"):
        lines.append(f"[roles.{role}]")
        lines.append(f'provider  = "{main_name}"')
        lines.append(f'model_env = "{main_prefix}_MODEL"')
        lines.append("")
    lines.append("[tools.memory]")
    lines.append("allow = []")
    lines.append("")
    lines.append("[tools.delegate]")
    lines.append('deny = ["delegate"]')
    return "\n".join(lines) + "\n"


_LEGACY_TEMPLATE = """\
# Legacy single-provider fallback. No *_API_KEY env vars were found
# at first run; edit .env to use MISTRAL_API_KEY / GLM_API_KEY / ...
# for multi-provider routing and regenerate this file.

default_role = "main"

[providers.default]
base_url_env = "BASE_URL"
api_key_env  = "API_KEY"

[roles.main]
provider  = "default"
model_env = "MODEL"
"""


def load_runtime_config(path: Path = CONFIG_FILE) -> RuntimeConfig:
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(_render_default_config(), encoding="utf-8")
        logger.info("created default config at %s", path)
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    cfg = RuntimeConfig.model_validate(data)
    cfg.register_providers()
    return cfg
