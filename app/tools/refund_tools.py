"""The four tools the agent can call. Thin adapters over repository, policy and gateway."""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal
from typing import Annotated, Any, Literal

from pydantic import BaseModel, Field, ValidationError

from app.config import Settings
from app.data.repository import DataQualityError, NotFoundError, Repository
from app.domain.policy import PolicyDecision, evaluate_refund
from app.tools.payment_gateway import FakePaymentGateway, GatewayUnavailable
from app.tools.registry import ErrorCode, Tool, ToolFailure, ToolRegistry

OrderId = Annotated[str, Field(pattern=r"^ORD-\d{4}$", description="Order id, e.g. ORD-1001")]


class OrderArgs(BaseModel):
    order_id: OrderId


class CustomerArgs(BaseModel):
    customer_id: Annotated[str, Field(pattern=r"^C-\d{3}$", description="Customer id, e.g. C-001")]


class RefundArgs(BaseModel):
    order_id: OrderId
    amount: Annotated[Decimal, Field(gt=0, max_digits=8, decimal_places=2)]
    reason: Annotated[str, Field(min_length=3, max_length=200)]


class RefundReceipt(BaseModel):
    """Contract we expect back from the payment provider."""

    refund_id: Annotated[str, Field(pattern=r"^RF-\d{6}$")]
    status: Literal["succeeded"]
    amount: Decimal


class RefundTools:
    def __init__(self, repo: Repository, gateway: FakePaymentGateway, settings: Settings) -> None:
        self.repo = repo
        self.gateway = gateway
        self.settings = settings

    def _load(self, fn, key: str):
        try:
            return fn(key)
        except NotFoundError as exc:
            raise ToolFailure(ErrorCode.NOT_FOUND, str(exc)) from exc
        except DataQualityError as exc:
            raise ToolFailure(ErrorCode.DATA_QUALITY, str(exc)) from exc

    def _decide(self, order_id: str) -> PolicyDecision:
        order = self._load(self.repo.get_order, order_id)
        customer = self._load(self.repo.get_customer, order.customer_id)
        return evaluate_refund(
            order,
            customer,
            now=self.settings.reference_time,
            stale_after=timedelta(hours=self.settings.stale_after_hours),
            max_auto_refund=Decimal(str(self.settings.max_auto_refund)),
        )

    def lookup_order(self, args: OrderArgs) -> dict[str, Any]:
        return self._load(self.repo.get_order, args.order_id).model_dump(mode="json")

    def lookup_customer(self, args: CustomerArgs) -> dict[str, Any]:
        c = self._load(self.repo.get_customer, args.customer_id)
        return {
            "customer_id": c.customer_id,
            "name": c.name,
            "email": c.masked_email,
            "tier": c.tier,
            "refunds_last_90d": c.refunds_last_90d,
        }

    def check_refund_policy(self, args: OrderArgs) -> dict[str, Any]:
        return self._decide(args.order_id).model_dump(mode="json")

    def issue_refund(self, args: RefundArgs) -> dict[str, Any]:
        # Re-check server-side: the approval was for a proposal, the rules are still the authority.
        decision = self._decide(args.order_id)
        if not decision.eligible:
            raise ToolFailure(ErrorCode.POLICY_VIOLATION, "; ".join(decision.reasons))
        if args.amount > decision.max_refund:
            raise ToolFailure(
                ErrorCode.POLICY_VIOLATION,
                f"amount {args.amount} exceeds allowed refund {decision.max_refund}",
            )
        try:
            raw = self.gateway.refund(args.order_id, args.amount)
        except GatewayUnavailable as exc:
            raise ToolFailure(ErrorCode.UNAVAILABLE, str(exc)) from exc
        try:
            receipt = RefundReceipt.model_validate(raw)
        except ValidationError as exc:
            raise ToolFailure(
                ErrorCode.INVALID_RESPONSE, f"payment provider returned malformed receipt: {raw}"
            ) from exc
        if receipt.amount != args.amount:
            raise ToolFailure(ErrorCode.INVALID_RESPONSE, "receipt amount does not match request")
        self.repo.mark_refunded(args.order_id)
        return {"order_id": args.order_id, **receipt.model_dump(mode="json")}


def build_registry(
    repo: Repository, gateway: FakePaymentGateway, settings: Settings
) -> ToolRegistry:
    t = RefundTools(repo, gateway, settings)
    return ToolRegistry(
        [
            Tool("lookup_order", "Fetch an order by id.", OrderArgs, t.lookup_order),
            Tool(
                "lookup_customer",
                "Fetch a customer profile (email masked).",
                CustomerArgs,
                t.lookup_customer,
            ),
            Tool(
                "check_refund_policy",
                "Evaluate refund eligibility and the maximum refundable amount for an order.",
                OrderArgs,
                t.check_refund_policy,
            ),
            Tool(
                "issue_refund",
                "Issue a refund. Requires human operator approval; the run pauses until decided.",
                RefundArgs,
                t.issue_refund,
                requires_approval=True,
            ),
        ],
        timeout_seconds=settings.tool_timeout_seconds,
    )
