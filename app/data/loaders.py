"""Load raw source files, validate each record and quarantine the ones that fail.

A bad upstream record must never crash the service or silently reach the agent.
"""

from __future__ import annotations

import csv
import json
from dataclasses import dataclass, field
from pathlib import Path

from pydantic import ValidationError

from app.domain.models import Customer, DataQualityIssue, Order, RawCustomer, RawOrder


@dataclass
class LoadResult[T]:
    records: dict[str, T] = field(default_factory=dict)
    issues: list[DataQualityIssue] = field(default_factory=list)


def _errors(exc: ValidationError) -> list[str]:
    return [f"{'.'.join(map(str, e['loc']))}: {e['msg']}" for e in exc.errors()]


def load_orders(path: Path) -> LoadResult[Order]:
    result: LoadResult[Order] = LoadResult()
    for raw in json.loads(path.read_text()):
        record_id = raw.get("order_id") if isinstance(raw, dict) else None
        try:
            order = Order.from_raw(RawOrder.model_validate(raw))
        except ValidationError as exc:
            result.issues.append(
                DataQualityIssue(source=path.name, record_id=record_id, errors=_errors(exc))
            )
            continue
        result.records[order.order_id] = order
    return result


def load_customers(path: Path) -> LoadResult[Customer]:
    result: LoadResult[Customer] = LoadResult()
    with path.open(newline="") as fh:
        for row in csv.DictReader(fh):
            try:
                customer = Customer.model_validate(RawCustomer.model_validate(row).model_dump())
            except ValidationError as exc:
                result.issues.append(
                    DataQualityIssue(
                        source=path.name, record_id=row.get("customer_id"), errors=_errors(exc)
                    )
                )
                continue
            result.records[customer.customer_id] = customer
    return result
