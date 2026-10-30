"""Validated domain models. Everything downstream of the loader uses these types only."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field


class OrderStatus(StrEnum):
    SHIPPED = "shipped"
    DELIVERED = "delivered"
    CANCELLED = "cancelled"
    REFUNDED = "refunded"


class CustomerTier(StrEnum):
    STANDARD = "standard"
    SILVER = "silver"
    GOLD = "gold"


class RawOrder(BaseModel):
    """Shape of an order record as delivered by the upstream order system."""

    model_config = ConfigDict(extra="forbid")

    order_id: str = Field(pattern=r"^ORD-\d{4}$")
    customer_id: str = Field(pattern=r"^C-\d{3}$")
    item: str = Field(min_length=1)
    amount_cents: int = Field(gt=0, strict=True)  # never coerce "12.99"/"abc"
    currency: str = Field(pattern=r"^[A-Z]{3}$")
    status: OrderStatus
    purchased_at: AwareDatetime
    delivered_at: AwareDatetime | None
    last_synced_at: AwareDatetime


class Order(BaseModel):
    """Order as the application sees it: money in Decimal dollars, timezone-aware datetimes."""

    model_config = ConfigDict(frozen=True)

    order_id: str
    customer_id: str
    item: str
    amount: Decimal
    currency: str
    status: OrderStatus
    purchased_at: datetime
    delivered_at: datetime | None
    last_synced_at: datetime

    @classmethod
    def from_raw(cls, raw: RawOrder) -> Order:
        return cls(
            **raw.model_dump(exclude={"amount_cents"}),
            amount=(Decimal(raw.amount_cents) / 100).quantize(Decimal("0.01")),
        )


class RawCustomer(BaseModel):
    """CSV row from the CRM export (all fields arrive as strings)."""

    model_config = ConfigDict(extra="forbid")

    customer_id: str = Field(pattern=r"^C-\d{3}$")
    name: str = Field(min_length=1)
    email: str = Field(pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
    tier: CustomerTier
    refunds_last_90d: int = Field(ge=0)
    account_created: date


class Customer(RawCustomer):
    model_config = ConfigDict(frozen=True)

    @property
    def masked_email(self) -> str:
        """PII minimisation: the agent/LLM only ever sees a masked email."""
        local, _, domain = self.email.partition("@")
        return f"{local[0]}***@{domain}"


class DataQualityIssue(BaseModel):
    source: str
    record_id: str | None
    errors: list[str]
