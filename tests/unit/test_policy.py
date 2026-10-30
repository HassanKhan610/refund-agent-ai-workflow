"""Each refund rule is pinned by one test, using the frozen fixture clock."""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

import pytest

from app.domain.policy import evaluate_refund


@pytest.fixture
def decide(repo, settings):
    def _decide(order_id: str):
        order = repo.get_order(order_id)
        return evaluate_refund(
            order,
            repo.get_customer(order.customer_id),
            now=settings.reference_time,
            stale_after=timedelta(hours=settings.stale_after_hours),
            max_auto_refund=Decimal(str(settings.max_auto_refund)),
        )

    return _decide


def test_eligible_order(decide):
    d = decide("ORD-1001")
    assert d.eligible and d.max_refund == Decimal("129.99")


@pytest.mark.parametrize(
    ("order_id", "reason"),
    [
        ("ORD-1003", "exceeds agent limit"),
        ("ORD-1004", "more than 30 days"),
        ("ORD-1005", "only delivered orders"),
        ("ORD-1007", "stale"),
        ("ORD-1008", "fraud review"),
        ("ORD-1009", "already been refunded"),
    ],
)
def test_ineligible_orders_explain_why(decide, order_id, reason):
    d = decide(order_id)
    assert not d.eligible
    assert d.max_refund == Decimal("0.00")
    assert any(reason in r for r in d.reasons)


def test_boundary_exactly_30_days_is_still_eligible(repo, settings):
    order = repo.get_order("ORD-1001")
    order = order.model_copy(update={"delivered_at": settings.reference_time - timedelta(days=30)})
    d = evaluate_refund(
        order,
        repo.get_customer("C-001"),
        now=settings.reference_time,
        stale_after=timedelta(hours=24),
        max_auto_refund=Decimal("500"),
    )
    assert d.eligible
