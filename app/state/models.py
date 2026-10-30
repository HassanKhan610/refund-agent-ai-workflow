"""Run state: the agent's durable memory across steps and across the human approval pause."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field


def _now() -> datetime:
    return datetime.now(UTC)


class RunStatus(StrEnum):
    RUNNING = "running"
    AWAITING_APPROVAL = "awaiting_approval"
    COMPLETED = "completed"
    FAILED = "failed"
    BLOCKED = "blocked"  # stopped by input guardrail, no tools ran

    @property
    def terminal(self) -> bool:
        return self in {RunStatus.COMPLETED, RunStatus.FAILED, RunStatus.BLOCKED}


class StepKind(StrEnum):
    USER_INPUT = "user_input"
    GUARDRAIL = "guardrail"
    LLM_CALL = "llm_call"
    TOOL_CALL = "tool_call"
    TOOL_RESULT = "tool_result"
    APPROVAL_REQUESTED = "approval_requested"
    APPROVAL_DECISION = "approval_decision"
    FINAL_ANSWER = "final_answer"
    ERROR = "error"


class Step(BaseModel):
    kind: StepKind
    at: datetime = Field(default_factory=_now)
    data: dict[str, Any] = Field(default_factory=dict)


class PendingAction(BaseModel):
    tool_call_id: str
    tool: str
    arguments: dict[str, Any]


class RunState(BaseModel):
    id: str = Field(default_factory=lambda: f"run_{uuid.uuid4().hex[:12]}")
    status: RunStatus = RunStatus.RUNNING
    created_by: str
    user_message: str
    created_at: datetime = Field(default_factory=_now)
    updated_at: datetime = Field(default_factory=_now)
    messages: list[dict[str, Any]] = Field(default_factory=list)  # LLM transcript
    steps: list[Step] = Field(default_factory=list)  # human-readable trace
    pending_action: PendingAction | None = None
    final_answer: str | None = None
    error: str | None = None

    def record(self, kind: StepKind, **data: Any) -> None:
        self.steps.append(Step(kind=kind, data=data))
        self.updated_at = _now()

    def tool_results(self) -> list[dict[str, Any]]:
        return [s.data for s in self.steps if s.kind == StepKind.TOOL_RESULT]
