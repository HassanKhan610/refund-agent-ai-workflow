"""Data validation: representative source data is transformed correctly, bad records quarantined."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from decimal import Decimal

import pytest

from app.data.loaders import load_customers, load_orders
from app.domain.models import OrderStatus


def test_every_source_order_is_either_loaded_or_quarantined(settings):
    raw_ids = {r["order_id"] for r in json.loads((settings.data_dir / "orders.json").read_text())}
    result = load_orders(settings.data_dir / "orders.json")
    quarantined = {i.record_id for i in result.issues}
    assert set(result.records) | quarantined == raw_ids
    assert not set(result.records) & quarantined


@pytest.mark.parametrize(
    ("order_id", "expected"),
    [
        ("ORD-1001", Decimal("129.99")),
        ("ORD-1002", Decimal("45.50")),
        ("ORD-1003", Decimal("899.00")),
    ],
)
def test_amount_cents_converted_to_exact_decimal_dollars(settings, order_id, expected):
    order = load_orders(settings.data_dir / "orders.json").records[order_id]
    assert order.amount == expected
    assert order.amount.as_tuple().exponent == -2  # always two decimal places


def test_timestamps_parsed_as_timezone_aware_utc(settings):
    order = load_orders(settings.data_dir / "orders.json").records["ORD-1001"]
    assert order.delivered_at == datetime(2026, 10, 20, 14, 30, tzinfo=UTC)
    assert order.status is OrderStatus.DELIVERED


def test_undelivered_order_keeps_null_delivery_date(settings):
    assert load_orders(settings.data_dir / "orders.json").records["ORD-1005"].delivered_at is None


@pytest.mark.parametrize(
    ("record_id", "fragment"),
    [("ORD-1006", "valid integer"), ("ORD-1010", "greater than 0")],
)
def test_malformed_orders_are_quarantined_with_reason(settings, record_id, fragment):
    issues = {i.record_id: i for i in load_orders(settings.data_dir / "orders.json").issues}
    assert fragment in " ".join(issues[record_id].errors)


def test_amount_as_string_is_not_coerced(tmp_path):
    bad = {
        "order_id": "ORD-2000",
        "customer_id": "C-001",
        "item": "x",
        "amount_cents": "1299",
        "currency": "USD",
        "status": "delivered",
        "purchased_at": "2026-10-01T00:00:00Z",
        "delivered_at": None,
        "last_synced_at": "2026-10-01T00:00:00Z",
    }
    (tmp_path / "orders.json").write_text(json.dumps([bad]))
    result = load_orders(tmp_path / "orders.json")
    assert not result.records and result.issues[0].record_id == "ORD-2000"


def test_naive_timestamp_rejected(tmp_path):
    bad = {
        "order_id": "ORD-2001",
        "customer_id": "C-001",
        "item": "x",
        "amount_cents": 100,
        "currency": "USD",
        "status": "delivered",
        "purchased_at": "2026-10-01T00:00:00",
        "delivered_at": None,
        "last_synced_at": "2026-10-01T00:00:00Z",
    }
    (tmp_path / "orders.json").write_text(json.dumps([bad]))
    assert load_orders(tmp_path / "orders.json").issues


def test_customers_csv_parsed_and_bad_row_quarantined(settings):
    result = load_customers(settings.data_dir / "customers.csv")
    assert set(result.records) == {"C-001", "C-002", "C-003", "C-004", "C-005"}
    alice = result.records["C-001"]
    assert alice.refunds_last_90d == 0 and alice.tier == "gold"
    assert [i.record_id for i in result.issues] == ["C-006"]
    assert len(result.issues[0].errors) == 4


def test_email_is_masked_for_agent_consumption(settings):
    alice = load_customers(settings.data_dir / "customers.csv").records["C-001"]
    assert alice.masked_email == "a***@example.com"
