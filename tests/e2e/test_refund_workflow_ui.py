"""Critical user journey: see data -> ask agent -> review proposal -> approve -> verify outcome."""

from pathlib import Path

import pytest
from playwright.sync_api import Page, expect

pytestmark = pytest.mark.e2e
SHOTS = Path(__file__).resolve().parents[2] / "reports" / "screenshots"


def shot(page: Page, name: str) -> None:
    SHOTS.mkdir(parents=True, exist_ok=True)
    page.screenshot(path=SHOTS / f"{name}.png", full_page=True)


def test_operator_approves_refund_end_to_end(page: Page, live_server: str):
    page.goto(live_server)

    # 1. Source data is rendered correctly (transformed amount, stale flag, quarantine hidden).
    row = page.locator('[data-testid="order-row"][data-order-id="ORD-1001"]')
    expect(row.get_by_test_id("order-amount")).to_have_text("$129.99")
    expect(row.get_by_test_id("order-status")).to_have_text("delivered")
    expect(page.locator('[data-order-id="ORD-1007"] [data-testid="stale-flag"]')).to_be_visible()
    expect(page.locator('[data-order-id="ORD-1006"]')).to_have_count(0)
    shot(page, "01_orders_loaded")

    # 2. Agent proposes a refund and pauses for a human.
    page.get_by_test_id("message-input").fill("Please refund order ORD-1001, it arrived damaged.")
    page.get_by_test_id("submit-run").click()
    expect(page.get_by_test_id("run-status")).to_have_text("awaiting_approval")
    expect(page.get_by_test_id("pending-action")).to_contain_text(
        "issue_refund $129.99 on ORD-1001"
    )
    expect(page.get_by_test_id("trace")).to_contain_text("check_refund_policy")
    shot(page, "02_awaiting_approval")

    # 3. Operator approves; refund is issued and reflected in the data table.
    page.get_by_test_id("approve-btn").click()
    expect(page.get_by_test_id("run-status")).to_have_text("completed")
    expect(page.get_by_test_id("final-answer")).to_contain_text("RF-000001")
    expect(page.get_by_test_id("pending-action")).to_be_hidden()
    expect(row.get_by_test_id("order-status")).to_have_text("refunded")
    shot(page, "03_refund_completed")


def test_injected_gateway_failure_is_visible_to_operator(page: Page, live_server: str):
    page.goto(live_server)
    page.get_by_test_id("message-input").fill("Refund ORD-1002 please, wrong colour")
    page.get_by_test_id("submit-run").click()
    expect(page.get_by_test_id("run-status")).to_have_text("awaiting_approval")

    page.get_by_test_id("fault-select").select_option("unavailable")
    page.get_by_test_id("approve-btn").click()
    expect(page.get_by_test_id("final-answer")).to_contain_text("could not be completed")
    expect(page.get_by_test_id("trace")).to_contain_text("unavailable")
    expect(page.locator('[data-order-id="ORD-1002"] [data-testid="order-status"]')).to_have_text(
        "delivered"
    )
    shot(page, "04_gateway_failure_visible")


def test_validation_error_shown_in_ui(page: Page, live_server: str):
    page.goto(live_server)
    page.get_by_test_id("message-input").fill("   ")
    page.get_by_test_id("submit-run").click()
    expect(page.get_by_test_id("form-error")).to_contain_text("blank")
