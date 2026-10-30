"""Data validation at the edge: what the API (and therefore the UI) shows matches the source."""

import json

from tests.conftest import REQUESTER


def test_api_orders_match_source_records(client, settings):
    source = {r["order_id"]: r for r in json.loads((settings.data_dir / "orders.json").read_text())}
    shown = client.get("/api/orders", headers=REQUESTER).json()
    assert len(shown) == 8  # 10 source records - 2 quarantined
    for o in shown:
        raw = source[o["order_id"]]
        assert o["amount"] == f"{raw['amount_cents'] / 100:.2f}"
        assert o["status"] == raw["status"] and o["item"] == raw["item"]


def test_display_formatting(client):
    shown = {o["order_id"]: o for o in client.get("/api/orders", headers=REQUESTER).json()}
    assert shown["ORD-1003"]["amount_display"] == "$899.00"
    assert shown["ORD-1002"]["amount_display"] == "$45.50"


def test_quarantined_records_are_not_displayed(client):
    ids = {o["order_id"] for o in client.get("/api/orders", headers=REQUESTER).json()}
    assert not ids & {"ORD-1006", "ORD-1010"}


def test_stale_flag_matches_sync_age(client):
    shown = {o["order_id"]: o for o in client.get("/api/orders", headers=REQUESTER).json()}
    assert shown["ORD-1007"]["is_stale"] is True
    assert sum(o["is_stale"] for o in shown.values()) == 1


def test_data_quality_report_lists_quarantined_records(client):
    from tests.conftest import OPERATOR

    issues = client.get("/api/data-quality", headers=OPERATOR).json()
    assert {i["record_id"] for i in issues} == {"ORD-1006", "ORD-1010", "C-006"}
