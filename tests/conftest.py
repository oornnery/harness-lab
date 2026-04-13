from __future__ import annotations

from pathlib import Path

import pytest

from src.context import WorkspaceContext
from src.model import HarnessSettings
from src.policy import HarnessDeps, RuntimePolicy
from src.sessions import SessionStore


@pytest.fixture
def harness_settings(tmp_path: Path) -> HarnessSettings:
    return HarnessSettings(
        workspace=tmp_path,
        session_dir=tmp_path / ".harness",
        api_key="sk-test",
    )


@pytest.fixture
def session_store(harness_settings: HarnessSettings) -> SessionStore:
    return SessionStore(harness_settings.resolved_session_dir())


@pytest.fixture
def workspace_context(tmp_path: Path) -> WorkspaceContext:
    (tmp_path / "hello.txt").write_text("hello world", encoding="utf-8")
    return WorkspaceContext(root=tmp_path)


@pytest.fixture
async def harness_deps(
    harness_settings: HarnessSettings,
    session_store: SessionStore,
    workspace_context: WorkspaceContext,
) -> HarnessDeps:
    session_id = session_store.create_session_id()
    await session_store.ensure_session(session_id)
    return HarnessDeps(
        settings=harness_settings,
        workspace=workspace_context,
        session_store=session_store,
        session_id=session_id,
        policy=RuntimePolicy(harness_settings, workspace_context.root),
    )
