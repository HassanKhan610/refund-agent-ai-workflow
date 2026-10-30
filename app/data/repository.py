"""Read-side data access. Tools depend on this interface, never on files directly."""

from __future__ import annotations

from pathlib import Path

from app.data.loaders import load_customers, load_orders
from app.domain.models import Customer, DataQualityIssue, Order, OrderStatus


class NotFoundError(LookupError):
    pass


class DataQualityError(ValueError):
    """The record exists upstream but failed validation, so it is unsafe to act on."""


class Repository:
    def __init__(self, data_dir: Path) -> None:
        orders = load_orders(data_dir / "orders.json")
        customers = load_customers(data_dir / "customers.csv")
        self._orders = orders.records
        self._customers = customers.records
        self.issues: list[DataQualityIssue] = orders.issues + customers.issues
        self._quarantined = {i.record_id: i for i in self.issues if i.record_id}

    def list_orders(self) -> list[Order]:
        return sorted(self._orders.values(), key=lambda o: o.order_id)

    def get_order(self, order_id: str) -> Order:
        return self._get(self._orders, order_id, "order")

    def get_customer(self, customer_id: str) -> Customer:
        return self._get(self._customers, customer_id, "customer")

    def _get[T](self, table: dict[str, T], key: str, kind: str) -> T:
        if key in table:
            return table[key]
        if key in self._quarantined:
            raise DataQualityError(
                f"{kind} {key} failed validation: {'; '.join(self._quarantined[key].errors)}"
            )
        raise NotFoundError(f"{kind} {key} not found")

    def mark_refunded(self, order_id: str) -> None:
        order = self.get_order(order_id)
        self._orders[order_id] = order.model_copy(update={"status": OrderStatus.REFUNDED})
