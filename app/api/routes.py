"""HTTP layer: translate requests into agent/repository calls. No business logic here."""

from __future__ import annotations

from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException, Request, status

from app.api.auth import Principal, require
from app.api.schemas import (
    ApproveRequest,
    CreateRunRequest,
    FaultRequest,
    Health,
    OrderView,
    RejectRequest,
    RunSummary,
    RunView,
)
from app.state.models import RunState
from app.state.store import InvalidTransition, RunNotFound

router = APIRouter()


def _c(request: Request):
    return request.app.state.container


def _visible_run(request: Request, run_id: str, p: Principal) -> RunState:
    try:
        run = _c(request).store.get(run_id)
    except RunNotFound:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "run not found") from None
    if not p.can("runs:read_all") and run.created_by != p.key_id:
        # 404 rather than 403: don't reveal that someone else's run exists.
        raise HTTPException(status.HTTP_404_NOT_FOUND, "run not found")
    return run


@router.get("/health", response_model=Health, tags=["ops"])
def health(request: Request) -> Health:
    c = _c(request)
    return Health(
        status="ok",
        llm=c.agent.llm.name,
        data_quality_issues=len(c.repo.issues),
        details={"payment_gateway": c.gateway.mode},
    )


@router.get("/api/orders", response_model=list[OrderView])
def list_orders(request: Request, _: Principal = Depends(require("orders:read"))):
    c = _c(request)
    stale_after = timedelta(hours=c.settings.stale_after_hours)
    now = c.settings.reference_time
    return [
        OrderView.build(o, stale=now - o.last_synced_at > stale_after) for o in c.repo.list_orders()
    ]


@router.get("/api/data-quality")
def data_quality(request: Request, _: Principal = Depends(require("admin"))):
    return [i.model_dump() for i in _c(request).repo.issues]


@router.post("/api/runs", response_model=RunView, status_code=status.HTTP_201_CREATED)
def create_run(
    body: CreateRunRequest, request: Request, p: Principal = Depends(require("runs:create"))
):
    return RunView.build(_c(request).agent.start(body.message, user=p.key_id))


@router.get("/api/runs", response_model=list[RunSummary])
def list_runs(request: Request, p: Principal = Depends(require("runs:read_own"))):
    runs = _c(request).store.list()
    if not p.can("runs:read_all"):
        runs = [r for r in runs if r.created_by == p.key_id]
    return [RunSummary(**r.model_dump()) for r in runs]


@router.get("/api/runs/{run_id}", response_model=RunView)
def get_run(run_id: str, request: Request, p: Principal = Depends(require("runs:read_own"))):
    return RunView.build(_visible_run(request, run_id, p))


def _decide(request: Request, run_id: str, p: Principal, approve: bool, reason: str) -> RunView:
    _visible_run(request, run_id, p)
    try:
        run = _c(request).agent.decide(run_id, approve=approve, operator=p.key_id, reason=reason)
    except InvalidTransition as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from None
    return RunView.build(run)


@router.post("/api/runs/{run_id}/approve", response_model=RunView)
def approve(
    run_id: str,
    body: ApproveRequest,
    request: Request,
    p: Principal = Depends(require("runs:decide")),
):
    return _decide(request, run_id, p, True, body.reason)


@router.post("/api/runs/{run_id}/reject", response_model=RunView)
def reject(
    run_id: str,
    body: RejectRequest,
    request: Request,
    p: Principal = Depends(require("runs:decide")),
):
    return _decide(request, run_id, p, False, body.reason)


@router.post("/api/admin/faults", tags=["ops"])
def set_faults(body: FaultRequest, request: Request, _: Principal = Depends(require("admin"))):
    c = _c(request)
    if not c.settings.enable_fault_injection:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "fault injection disabled")
    c.gateway.mode = body.payment_gateway
    return {"payment_gateway": c.gateway.mode}
