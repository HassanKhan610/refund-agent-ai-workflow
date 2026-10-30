"""LLM clients behind one interface: a deterministic mock and OpenRouter (OpenAI-compatible)."""

from __future__ import annotations

import json
import logging
import re
import time
import uuid
from decimal import Decimal
from typing import Any, Protocol

import httpx
from pydantic import BaseModel, Field

from app.config import Settings

log = logging.getLogger(__name__)


class ToolCall(BaseModel):
    id: str = Field(default_factory=lambda: f"call_{uuid.uuid4().hex[:8]}")
    name: str
    arguments: dict[str, Any]


class LLMResponse(BaseModel):
    content: str | None = None
    tool_calls: list[ToolCall] = Field(default_factory=list)

    def as_message(self) -> dict[str, Any]:
        msg: dict[str, Any] = {"role": "assistant", "content": self.content or ""}
        if self.tool_calls:
            msg["tool_calls"] = [
                {
                    "id": c.id,
                    "type": "function",
                    "function": {"name": c.name, "arguments": json.dumps(c.arguments)},
                }
                for c in self.tool_calls
            ]
        return msg


class LLMError(Exception):
    pass


class LLMClient(Protocol):
    name: str

    def complete(
        self, messages: list[dict[str, Any]], tools: list[dict[str, Any]]
    ) -> LLMResponse: ...


# --------------------------------------------------------------------------- OpenRouter


class OpenRouterClient:
    URL = "https://openrouter.ai/api/v1/chat/completions"
    RETRY_STATUS = {408, 429, 500, 502, 503, 504}

    def __init__(self, api_key: str, model: str, timeout: float, max_retries: int = 2) -> None:
        if not api_key:
            raise ValueError("OPENROUTER_API_KEY is required when LLM_MODE=live")
        self.name = f"openrouter:{model}"
        self._model = model
        self._max_retries = max_retries
        self._http = httpx.Client(
            timeout=timeout,
            headers={"Authorization": f"Bearer {api_key}", "X-Title": "refund-agent"},
        )

    def complete(self, messages: list[dict[str, Any]], tools: list[dict[str, Any]]) -> LLMResponse:
        payload = {"model": self._model, "messages": messages, "tools": tools, "temperature": 0}
        last_error = "unknown error"
        for attempt in range(self._max_retries + 1):
            try:
                resp = self._http.post(self.URL, json=payload)
            except httpx.TimeoutException:
                last_error = "model request timed out"
            except httpx.HTTPError as exc:
                last_error = f"network error: {type(exc).__name__}"
            else:
                if resp.status_code == 200:
                    return self._parse(resp.json())
                last_error = f"provider returned HTTP {resp.status_code}"
                if resp.status_code not in self.RETRY_STATUS:
                    break
            log.warning("llm_retry", extra={"attempt": attempt, "error": last_error})
            time.sleep(min(2**attempt, 8))
        raise LLMError(last_error)

    @staticmethod
    def _parse(body: dict[str, Any]) -> LLMResponse:
        try:
            msg = body["choices"][0]["message"]
        except (KeyError, IndexError, TypeError) as exc:
            raise LLMError(f"malformed provider response: {str(body)[:200]}") from exc
        calls = []
        for c in msg.get("tool_calls") or []:
            raw = c.get("function", {}).get("arguments") or "{}"
            try:
                args = json.loads(raw)
            except json.JSONDecodeError:
                args = {"_unparseable_arguments": raw}  # registry will reject -> model can retry
            calls.append(
                ToolCall(
                    id=c.get("id") or ToolCall().id,
                    name=c.get("function", {}).get("name", ""),
                    arguments=args,
                )
            )
        return LLMResponse(content=msg.get("content"), tool_calls=calls)


# --------------------------------------------------------------------------- Mock

ORDER_RE = re.compile(r"\bORD-\d{4}\b")
AMOUNT_RE = re.compile(r"\$\s?(\d+(?:\.\d{1,2})?)")
REFUND_INTENT = re.compile(r"refund|money back|reimburse|return (?:it|this|my)", re.I)


class MockLLM:
    """Deterministic planner that follows the same procedure the system prompt prescribes.

    It reads only the transcript (user text + tool results), exactly like a real model,
    so the orchestrator, tools, approval flow and evals are exercised end-to-end offline.
    `fault` injects model-side failures: "unavailable" | "malformed_tool_call".
    """

    name = "mock"

    def __init__(self, fault: str | None = None) -> None:
        self.fault = fault

    def complete(self, messages: list[dict[str, Any]], tools: list[dict[str, Any]]) -> LLMResponse:
        if self.fault == "unavailable":
            raise LLMError("provider returned HTTP 503")
        user = next(m["content"] for m in messages if m["role"] == "user")
        results = _tool_results(messages)
        order_ids = ORDER_RE.findall(user)

        if not order_ids:
            return _say("I need an order id (for example ORD-1001) before I can help.")
        if len(set(order_ids)) > 1:
            return _say("I can only handle one order per request. Which order should I start with?")
        oid = order_ids[0]
        wants_refund = bool(REFUND_INTENT.search(user))

        order = results.get("lookup_order")
        if order is None:
            if self.fault == "malformed_tool_call":
                return _call("lookup_order", {"order": oid.removeprefix("ORD-")})
            return _call("lookup_order", {"order_id": oid})
        if not order["ok"]:
            if (
                order["error"]["code"] == "invalid_arguments"
                and results.retries("lookup_order") < 2
            ):
                return _call("lookup_order", {"order_id": oid})
            return _say(
                f"I couldn't retrieve order {oid}: {order['error']['message']}. "
                "No action was taken."
            )
        o = order["data"]
        if not wants_refund:
            return _say(f"Order {oid} ({o['item']}, ${o['amount']}) is currently {o['status']}.")

        customer = results.get("lookup_customer")
        if customer is None:
            return _call("lookup_customer", {"customer_id": o["customer_id"]})
        if not customer["ok"]:
            return _say(
                f"I couldn't verify the customer for {oid}: "
                f"{customer['error']['message']}. No action was taken."
            )

        policy = results.get("check_refund_policy")
        if policy is None:
            return _call("check_refund_policy", {"order_id": oid})
        if not policy["ok"]:
            return _say(
                f"I couldn't check the refund policy for {oid}: "
                f"{policy['error']['message']}. No action was taken."
            )
        p = policy["data"]
        if not p["eligible"]:
            return _say(
                f"Order {oid} is not eligible for an automatic refund: "
                f"{'; '.join(p['reasons'])}. No refund was issued."
            )

        refund = results.get("issue_refund")
        if refund is None:
            max_refund = Decimal(p["max_refund"])
            m = AMOUNT_RE.search(user)
            amount = min(Decimal(m.group(1)), max_refund) if m else max_refund
            return _call(
                "issue_refund",
                {
                    "order_id": oid,
                    "amount": f"{amount:.2f}",
                    "reason": user.strip()[:200] or "customer request",
                },
            )
        if refund["ok"]:
            r = refund["data"]
            return _say(
                f"Refund {r['refund_id']} for ${r['amount']} on order {oid} "
                f"({o['item']}) was issued successfully."
            )
        err = refund["error"]
        if err["code"] == "rejected_by_operator":
            return _say(
                f"The operator declined the refund for {oid} ({err['message']}). "
                "No refund was issued."
            )
        return _say(
            f"The refund for {oid} could not be completed: {err['message']}. "
            "No money was moved; please retry later or escalate to finance."
        )


class _Results(dict):
    def __init__(self) -> None:
        super().__init__()
        self.counts: dict[str, int] = {}

    def retries(self, tool: str) -> int:
        return self.counts.get(tool, 0)


def _tool_results(messages: list[dict[str, Any]]) -> _Results:
    """Latest result per tool name, reconstructed from the transcript."""
    names: dict[str, str] = {}
    out = _Results()
    for m in messages:
        for c in m.get("tool_calls") or []:
            names[c["id"]] = c["function"]["name"]
        if m["role"] == "tool":
            name = names.get(m["tool_call_id"], "")
            out[name] = json.loads(m["content"])
            out.counts[name] = out.counts.get(name, 0) + 1
    return out


def _say(text: str) -> LLMResponse:
    return LLMResponse(content=text)


def _call(name: str, args: dict[str, Any]) -> LLMResponse:
    return LLMResponse(tool_calls=[ToolCall(name=name, arguments=args)])


def build_llm(settings: Settings) -> LLMClient:
    if settings.llm_mode == "live":
        return OpenRouterClient(
            settings.openrouter_api_key, settings.openrouter_model, settings.llm_timeout_seconds
        )
    return MockLLM()
