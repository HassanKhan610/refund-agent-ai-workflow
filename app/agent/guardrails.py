"""Cheap, deterministic input/output guardrails. They run before and after the model."""

from __future__ import annotations

import re
from typing import Any

from pydantic import BaseModel

INJECTION = re.compile(
    r"ignore (?:all |any |the )?(?:previous|prior|above|your) (?:instructions|rules)"
    r"|disregard (?:the |your )?(?:rules|instructions|policy)"
    r"|system prompt|developer mode|you are now|jailbreak"
    r"|(?:without|skip|bypass)(?: the| any)? (?:approval|operator|policy)",
    re.I,
)
UNSAFE = re.compile(
    r"refund (?:all|every)(?: the)? orders?"
    r"|(?:all|every) customers?|(?:list|export|dump|show)\b.*\b(?:emails?|customers|card numbers?)",
    re.I,
)
EMAIL = re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.]+\b")
REFUND_ID = re.compile(r"\bRF-\d{6}\b")


class GuardrailVerdict(BaseModel):
    allowed: bool
    category: str | None = None
    reason: str | None = None


def check_input(text: str) -> GuardrailVerdict:
    if INJECTION.search(text):
        return GuardrailVerdict(
            allowed=False,
            category="prompt_injection",
            reason="request tries to override agent rules or approvals",
        )
    if UNSAFE.search(text):
        return GuardrailVerdict(
            allowed=False,
            category="unsafe_scope",
            reason="bulk actions or other customers' data are out of scope",
        )
    return GuardrailVerdict(allowed=True)


def check_output(answer: str, tool_results: list[dict[str, Any]]) -> tuple[str, list[str]]:
    """Redact PII and block refund ids that no successful tool call produced."""
    violations: list[str] = []
    if EMAIL.search(answer):
        answer = EMAIL.sub("[redacted email]", answer)
        violations.append("pii_email_redacted")
    issued = {
        r["output"]["data"]["refund_id"]
        for r in tool_results
        if r.get("tool") == "issue_refund" and r["output"].get("ok")
    }
    claimed = set(REFUND_ID.findall(answer))
    if claimed - issued:
        violations.append("ungrounded_refund_id")
        answer = (
            "I could not verify that a refund was issued, so I am not reporting one. "
            "An operator should review this run."
        )
    return answer, violations
