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


REQUESTER = {"X-API-Key": "dev-requester-key"}
OPERATOR = {"X-API-Key": "dev-operator-key"}


@pytest.fixture
def make_client():
    from dataclasses import replace

    from fastapi.testclient import TestClient

    from app.main import create_app

    def _make(llm=None, **overrides):
        settings = replace(Settings(), tool_timeout_seconds=0.2, **overrides)
        app = create_app(settings, llm=llm, persist_traces=False)
        app.state.container.gateway.hang_seconds = 1.0
        return TestClient(app, raise_server_exceptions=False)

    return _make


@pytest.fixture
def client(make_client):
    return make_client()
