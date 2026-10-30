"""API contracts. Internal state (LLM transcript, system prompt) is never exposed."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.domain.models import Order
from app.state.models import PendingAction, RunState, RunStatus, Step
from app.tools.payment_gateway import GatewayMode


class CreateRunRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    message: str = Field(min_length=1, max_length=1000)

    @field_validator("message")
    @classmethod
    def _not_blank(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("message must not be blank")
        return v.strip()


class ApproveRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    reason: str = Field(default="", max_length=500)


class RejectRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    reason: str = Field(min_length=3, max_length=500)


class FaultRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    payment_gateway: GatewayMode


class OrderView(BaseModel):
    order_id: str
    customer_id: str
    item: str
    amount: str
    amount_display: str
    status: str
    delivered_at: datetime | None
    last_synced_at: datetime
    is_stale: bool

    @classmethod
    def build(cls, o: Order, *, stale: bool) -> OrderView:
        return cls(
            order_id=o.order_id,
            customer_id=o.customer_id,
            item=o.item,
            amount=f"{o.amount:.2f}",
            amount_display=f"${o.amount:,.2f}",
            status=o.status,
            delivered_at=o.delivered_at,
            last_synced_at=o.last_synced_at,
            is_stale=stale,
        )


class RunView(BaseModel):
    id: str
    status: RunStatus
    created_by: str
    user_message: str
    created_at: datetime
    updated_at: datetime
    pending_action: PendingAction | None
    final_answer: str | None
    error: str | None
    steps: list[Step]

    @classmethod
    def build(cls, run: RunState) -> RunView:
        return cls(**run.model_dump(exclude={"messages"}))


class RunSummary(BaseModel):
    id: str
    status: RunStatus
    user_message: str
    created_at: datetime


class Health(BaseModel):
    status: str
    llm: str
    data_quality_issues: int
    details: dict[str, Any] = {}
