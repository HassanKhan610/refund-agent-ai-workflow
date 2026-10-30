"""Composition root: build dependencies once and hand them to the HTTP layer."""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse

from app.agent.llm import LLMClient, build_llm
from app.agent.orchestrator import Agent
from app.api.routes import router
from app.config import Settings
from app.data.repository import Repository
from app.logging_config import configure_logging, request_id_var
from app.state.store import RunStore
from app.tools.payment_gateway import FakePaymentGateway
from app.tools.refund_tools import build_registry

log = logging.getLogger(__name__)
STATIC = Path(__file__).parent / "ui" / "static"


@dataclass
class Container:
    settings: Settings
    repo: Repository
    gateway: FakePaymentGateway
    store: RunStore
    agent: Agent


def build_container(
    settings: Settings, llm: LLMClient | None = None, trace_dir: Path | None = None
) -> Container:
    repo = Repository(settings.data_dir)
    gateway = FakePaymentGateway()
    store = RunStore(trace_dir=trace_dir)
    registry = build_registry(repo, gateway, settings)
    agent = Agent(llm or build_llm(settings), registry, store, settings.max_agent_steps)
    return Container(settings, repo, gateway, store, agent)


def create_app(
    settings: Settings | None = None, llm: LLMClient | None = None, persist_traces: bool = True
) -> FastAPI:
    settings = settings or Settings.from_env()
    configure_logging()
    app = FastAPI(title="Refund Agent", version="0.1.0")
    app.state.container = build_container(
        settings, llm, settings.trace_dir if persist_traces else None
    )

    @app.middleware("http")
    async def request_context(request: Request, call_next):
        rid = request.headers.get("x-request-id") or uuid.uuid4().hex[:12]
        token = request_id_var.set(rid)
        try:
            response = await call_next(request)
        except Exception:
            log.exception("unhandled_error", extra={"path": request.url.path})
            response = JSONResponse({"detail": "internal error", "request_id": rid}, 500)
        finally:
            request_id_var.reset(token)
        response.headers["x-request-id"] = rid
        return response

    app.include_router(router)

    @app.get("/", include_in_schema=False)
    def index() -> FileResponse:
        return FileResponse(STATIC / "index.html")

    return app
