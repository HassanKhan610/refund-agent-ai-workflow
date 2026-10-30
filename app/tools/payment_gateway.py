"""Fake payment provider with switchable failure modes (our injected-failure surface)."""

from __future__ import annotations

import itertools
import threading
import time
from decimal import Decimal
from enum import StrEnum
from typing import Any


class GatewayMode(StrEnum):
    OK = "ok"
    TIMEOUT = "timeout"  # hangs longer than the tool timeout
    UNAVAILABLE = "unavailable"  # dependency down (HTTP 503 equivalent)
    INVALID_RESPONSE = "invalid_response"  # 200 OK with a malformed body


class GatewayUnavailable(Exception):
    pass


class FakePaymentGateway:
    def __init__(self, mode: GatewayMode = GatewayMode.OK, hang_seconds: float = 5.0) -> None:
        self.mode = mode
        self.hang_seconds = hang_seconds
        self.calls: list[tuple[str, Decimal]] = []
        self._ids = itertools.count(1)
        self._lock = threading.Lock()

    def refund(self, order_id: str, amount: Decimal) -> dict[str, Any]:
        with self._lock:
            self.calls.append((order_id, amount))
        match self.mode:
            case GatewayMode.TIMEOUT:
                time.sleep(self.hang_seconds)
                return {}
            case GatewayMode.UNAVAILABLE:
                raise GatewayUnavailable("payment provider returned 503")
            case GatewayMode.INVALID_RESPONSE:
                return {"status": "ok", "ref": None}
        with self._lock:
            refund_id = f"RF-{next(self._ids):06d}"
        return {"refund_id": refund_id, "status": "succeeded", "amount": str(amount)}
