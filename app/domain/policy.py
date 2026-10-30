"""Refund policy: deterministic business rules. The LLM never decides eligibility."""

from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal

from pydantic import BaseModel

from app.domain.models import Customer, Order, OrderStatus

REFUND_WINDOW_DAYS = 30
MAX_REFUNDS_90D = 3


class PolicyDecision(BaseModel):
    order_id: str
    eligible: bool
    max_refund: Decimal
    reasons: list[str]


def evaluate_refund(
    order: Order,
    customer: Customer,
    *,
    now: datetime,
    stale_after: timedelta,
    max_auto_refund: Decimal,
) -> PolicyDecision:
    reasons: list[str] = []

    if now - order.last_synced_at > stale_after:
        reasons.append(
            f"order data is stale (last synced {order.last_synced_at.isoformat()}); "
            "re-sync before deciding"
        )
    if order.status == OrderStatus.REFUNDED:
        reasons.append("order has already been refunded")
    elif order.status != OrderStatus.DELIVERED or order.delivered_at is None:
        reasons.append(f"order is '{order.status}', only delivered orders can be refunded")
    elif now - order.delivered_at > timedelta(days=REFUND_WINDOW_DAYS):
        reasons.append(f"delivered more than {REFUND_WINDOW_DAYS} days ago")
    if order.amount > max_auto_refund:
        reasons.append(
            f"amount ${order.amount} exceeds agent limit ${max_auto_refund}; escalate to finance"
        )
    if customer.refunds_last_90d >= MAX_REFUNDS_90D:
        reasons.append(
            f"customer has {customer.refunds_last_90d} refunds in 90 days; needs fraud review"
        )

    eligible = not reasons
    return PolicyDecision(
        order_id=order.order_id,
        eligible=eligible,
        max_refund=order.amount if eligible else Decimal("0.00"),
        reasons=reasons or ["meets refund policy"],
    )
