"""Tool registry: schema export for the LLM, argument validation, timeouts, typed errors."""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeout
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ValidationError

log = logging.getLogger(__name__)


class ErrorCode(StrEnum):
    UNKNOWN_TOOL = "unknown_tool"
    INVALID_ARGUMENTS = "invalid_arguments"
    APPROVAL_REQUIRED = "approval_required"
    NOT_FOUND = "not_found"
    DATA_QUALITY = "data_quality"
    POLICY_VIOLATION = "policy_violation"
    TIMEOUT = "timeout"
    UNAVAILABLE = "unavailable"
    INVALID_RESPONSE = "invalid_response"
    REJECTED_BY_OPERATOR = "rejected_by_operator"
    INTERNAL = "internal"


RETRYABLE = {ErrorCode.TIMEOUT, ErrorCode.UNAVAILABLE}


class ToolFailure(Exception):
    """Raised by tool handlers to report an expected, typed failure."""

    def __init__(self, code: ErrorCode, message: str) -> None:
        super().__init__(message)
        self.code = code


class ToolError(BaseModel):
    code: ErrorCode
    message: str
    retryable: bool


class ToolResult(BaseModel):
    tool: str
    ok: bool
    data: dict[str, Any] | None = None
    error: ToolError | None = None
    duration_ms: int = 0

    def for_llm(self) -> dict[str, Any]:
        return (
            {"ok": True, "data": self.data}
            if self.ok
            else {"ok": False, "error": self.error.model_dump(mode="json") if self.error else None}
        )


@dataclass(frozen=True)
class Tool:
    name: str
    description: str
    input_model: type[BaseModel]
    handler: Callable[[Any], dict[str, Any]]
    requires_approval: bool = False

    def schema(self) -> dict[str, Any]:
        """OpenAI/OpenRouter function-calling schema."""
        params = self.input_model.model_json_schema()
        params.pop("title", None)
        return {
            "type": "function",
            "function": {"name": self.name, "description": self.description, "parameters": params},
        }


class ToolRegistry:
    def __init__(self, tools: list[Tool], timeout_seconds: float) -> None:
        self._tools = {t.name: t for t in tools}
        self._timeout = timeout_seconds
        self._pool = ThreadPoolExecutor(max_workers=4, thread_name_prefix="tool")

    def get(self, name: str) -> Tool | None:
        return self._tools.get(name)

    def schemas(self) -> list[dict[str, Any]]:
        return [t.schema() for t in self._tools.values()]

    def execute(self, name: str, args: dict[str, Any], *, approved: bool = False) -> ToolResult:
        start = time.perf_counter()
        result = self._execute(name, args, approved)
        result.duration_ms = int((time.perf_counter() - start) * 1000)
        log.info(
            "tool_executed",
            extra={
                "tool": name,
                "ok": result.ok,
                "duration_ms": result.duration_ms,
                "error_code": result.error.code if result.error else None,
            },
        )
        return result

    def _execute(self, name: str, args: dict[str, Any], approved: bool) -> ToolResult:
        tool = self._tools.get(name)
        if tool is None:
            return failure(name, ErrorCode.UNKNOWN_TOOL, f"no tool named '{name}'")
        if tool.requires_approval and not approved:
            return failure(name, ErrorCode.APPROVAL_REQUIRED, f"'{name}' needs operator approval")
        try:
            parsed = tool.input_model.model_validate(args)
        except ValidationError as exc:
            msg = "; ".join(f"{'.'.join(map(str, e['loc']))}: {e['msg']}" for e in exc.errors())
            return failure(name, ErrorCode.INVALID_ARGUMENTS, msg)

        future = self._pool.submit(tool.handler, parsed)
        try:
            data = future.result(timeout=self._timeout)
        except FutureTimeout:
            # The worker thread may still finish later; side-effecting tools must be idempotent.
            return failure(name, ErrorCode.TIMEOUT, f"'{name}' timed out after {self._timeout}s")
        except ToolFailure as exc:
            return failure(name, exc.code, str(exc))
        except Exception as exc:  # unexpected bug: contain it, log it, never leak a traceback
            log.exception("tool_crashed", extra={"tool": name})
            return failure(
                name, ErrorCode.INTERNAL, f"internal error in '{name}': {type(exc).__name__}"
            )
        return ToolResult(tool=name, ok=True, data=data)


def failure(tool: str, code: ErrorCode, message: str) -> ToolResult:
    return ToolResult(
        tool=tool,
        ok=False,
        error=ToolError(code=code, message=message, retryable=code in RETRYABLE),
    )
