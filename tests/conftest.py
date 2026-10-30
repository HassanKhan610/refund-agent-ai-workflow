from __future__ import annotations

import pytest

from app.config import Settings
from app.data.repository import Repository


@pytest.fixture
def settings() -> Settings:
    return Settings()


@pytest.fixture
def repo(settings: Settings) -> Repository:
    return Repository(settings.data_dir)


@pytest.fixture
def fast_settings() -> Settings:
    """Short tool timeout so injected gateway hangs fail fast in tests."""
    from dataclasses import replace

    return replace(Settings(), tool_timeout_seconds=0.2)


@pytest.fixture
def gateway():
    from app.tools.payment_gateway import FakePaymentGateway

    return FakePaymentGateway(hang_seconds=1.0)


@pytest.fixture
def registry(repo, gateway, fast_settings):
    from app.tools.refund_tools import build_registry

    return build_registry(repo, gateway, fast_settings)


@pytest.fixture
def make_agent(registry):
    from app.agent.llm import MockLLM
    from app.agent.orchestrator import Agent
    from app.state.store import RunStore

    def _make(llm=None, max_steps: int = 8):
        return Agent(llm or MockLLM(), registry, RunStore(), max_steps=max_steps)

    return _make
