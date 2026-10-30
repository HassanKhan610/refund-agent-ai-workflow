"""Business tools, including every injected payment-gateway failure."""

import pytest

from app.tools.payment_gateway import GatewayMode
from app.tools.registry import ErrorCode

REFUND = {"order_id": "ORD-1001", "amount": "129.99", "reason": "arrived damaged"}


def test_lookup_order_returns_json_safe_transformed_data(registry):
    r = registry.execute("lookup_order", {"order_id": "ORD-1001"})
    assert r.ok and r.data["amount"] == "129.99" and r.data["status"] == "delivered"


def test_lookup_customer_masks_email(registry):
    r = registry.execute("lookup_customer", {"customer_id": "C-001"})
    assert r.data["email"] == "a***@example.com"
    assert "alice.morgan" not in str(r.data)


@pytest.mark.parametrize(
    ("args", "code"),
    [
        ({"order_id": "ORD-9999"}, ErrorCode.NOT_FOUND),
        ({"order_id": "ORD-1006"}, ErrorCode.DATA_QUALITY),
        ({"order_id": "1001; DROP TABLE"}, ErrorCode.INVALID_ARGUMENTS),
    ],
)
def test_lookup_order_failures_are_typed(registry, args, code):
    assert registry.execute("lookup_order", args).error.code == code


def test_check_policy(registry):
    r = registry.execute("check_refund_policy", {"order_id": "ORD-1001"})
    assert r.data["eligible"] is True and r.data["max_refund"] == "129.99"


def test_issue_refund_success_marks_order_refunded(registry, repo, gateway):
    r = registry.execute("issue_refund", REFUND, approved=True)
    assert r.ok and r.data["refund_id"].startswith("RF-")
    assert repo.get_order("ORD-1001").status == "refunded"
    assert len(gateway.calls) == 1


def test_second_refund_is_blocked_by_policy(registry):
    registry.execute("issue_refund", REFUND, approved=True)
    r = registry.execute("issue_refund", REFUND, approved=True)
    assert r.error.code == ErrorCode.POLICY_VIOLATION


def test_refund_above_allowed_amount_rejected_before_gateway(registry, gateway):
    r = registry.execute("issue_refund", {**REFUND, "amount": "500.00"}, approved=True)
    assert r.error.code == ErrorCode.POLICY_VIOLATION and not gateway.calls


def test_refund_on_ineligible_order_rejected_even_if_approved(registry, gateway):
    r = registry.execute(
        "issue_refund",
        {"order_id": "ORD-1004", "amount": "29.99", "reason": "late"},
        approved=True,
    )
    assert r.error.code == ErrorCode.POLICY_VIOLATION and not gateway.calls


@pytest.mark.parametrize(
    ("mode", "code"),
    [
        (GatewayMode.TIMEOUT, ErrorCode.TIMEOUT),
        (GatewayMode.UNAVAILABLE, ErrorCode.UNAVAILABLE),
        (GatewayMode.INVALID_RESPONSE, ErrorCode.INVALID_RESPONSE),
    ],
)
def test_gateway_failures_never_mark_order_refunded(registry, repo, gateway, mode, code):
    gateway.mode = mode
    r = registry.execute("issue_refund", REFUND, approved=True)
    assert not r.ok and r.error.code == code
    assert repo.get_order("ORD-1001").status == "delivered"
