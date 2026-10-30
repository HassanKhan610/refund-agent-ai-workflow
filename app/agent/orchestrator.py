"""Agent orchestration: the LLM proposes, tools execute, humans approve, state persists."""

from __future__ import annotations

import json
import logging

from app.agent.guardrails import check_input, check_output
from app.agent.llm import LLMClient, LLMError, ToolCall
from app.agent.prompts import SYSTEM_PROMPT
from app.state.models import PendingAction, RunState, RunStatus, StepKind
from app.state.store import RunStore
from app.tools.registry import ErrorCode, ToolRegistry, ToolResult, failure

log = logging.getLogger(__name__)


class Agent:
    def __init__(
        self, llm: LLMClient, registry: ToolRegistry, store: RunStore, max_steps: int = 8
    ) -> None:
        self.llm = llm
        self.registry = registry
        self.store = store
        self.max_steps = max_steps

    # ------------------------------------------------------------------ public API

    def start(self, message: str, user: str) -> RunState:
        run = RunState(created_by=user, user_message=message)
        run.record(StepKind.USER_INPUT, text=message, user=user)
        verdict = check_input(message)
        if not verdict.allowed:
            run.record(StepKind.GUARDRAIL, **verdict.model_dump())
            run.status = RunStatus.BLOCKED
            run.final_answer = f"I can't help with that request: {verdict.reason}."
            log.warning("input_blocked", extra={"run_id": run.id, "category": verdict.category})
            self.store.save(run)
            return run
        run.messages = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": message},
        ]
        return self._loop(run)

    def decide(self, run_id: str, *, approve: bool, operator: str, reason: str = "") -> RunState:
        run = self.store.claim_pending(run_id)  # raises if not awaiting approval
        action = run.pending_action
        assert action is not None
        run.pending_action = None
        run.record(StepKind.APPROVAL_DECISION, approved=approve, operator=operator, reason=reason)
        log.info(
            "approval_decision", extra={"run_id": run.id, "approved": approve, "operator": operator}
        )
        if approve:
            result = self.registry.execute(action.tool, action.arguments, approved=True)
        else:
            result = failure(
                action.tool, ErrorCode.REJECTED_BY_OPERATOR, reason or "rejected by operator"
            )
        self._add_result(run, action.tool_call_id, action.tool, action.arguments, result)
        return self._loop(run)

    # ------------------------------------------------------------------ loop

    def _loop(self, run: RunState) -> RunState:
        while self._llm_calls(run) < self.max_steps:
            try:
                resp = self.llm.complete(run.messages, self.registry.schemas())
            except LLMError as exc:
                return self._fail(run, f"model unavailable: {exc}")
            run.record(
                StepKind.LLM_CALL,
                model=self.llm.name,
                content=resp.content,
                tool_calls=[c.model_dump() for c in resp.tool_calls],
            )
            run.messages.append(resp.as_message())

            if not resp.tool_calls:
                return self._complete(run, resp.content or "")

            # Run safe calls first; an approval-gated call pauses the run after them.
            calls = sorted(resp.tool_calls, key=self._needs_approval)
            for call in calls:
                run.record(StepKind.TOOL_CALL, id=call.id, tool=call.name, arguments=call.arguments)
                if self._needs_approval(call):
                    run.pending_action = PendingAction(
                        tool_call_id=call.id, tool=call.name, arguments=call.arguments
                    )
                    run.status = RunStatus.AWAITING_APPROVAL
                    run.record(
                        StepKind.APPROVAL_REQUESTED, tool=call.name, arguments=call.arguments
                    )
                    log.info("approval_requested", extra={"run_id": run.id, "tool": call.name})
                    self.store.save(run)
                    return run
                result = self._execute_with_retry(call)
                self._add_result(run, call.id, call.name, call.arguments, result)

        return self._fail(run, f"step budget of {self.max_steps} model calls exhausted")

    def _execute_with_retry(self, call: ToolCall) -> ToolResult:
        result = self.registry.execute(call.name, call.arguments)
        # Only read-only tools reach here (side-effecting ones are approval-gated), so a
        # single retry of transient errors can't cause duplicate side effects.
        if not result.ok and result.error and result.error.retryable:
            result = self.registry.execute(call.name, call.arguments)
        return result

    def _needs_approval(self, call: ToolCall) -> bool:
        tool = self.registry.get(call.name)
        return bool(tool and tool.requires_approval)

    @staticmethod
    def _llm_calls(run: RunState) -> int:
        return sum(1 for s in run.steps if s.kind == StepKind.LLM_CALL)

    @staticmethod
    def _add_result(run: RunState, call_id: str, tool: str, args: dict, result: ToolResult) -> None:
        output = result.for_llm()
        run.record(
            StepKind.TOOL_RESULT,
            id=call_id,
            tool=tool,
            arguments=args,
            output=output,
            duration_ms=result.duration_ms,
        )
        run.messages.append(
            {"role": "tool", "tool_call_id": call_id, "content": json.dumps(output)}
        )

    def _complete(self, run: RunState, answer: str) -> RunState:
        answer, violations = check_output(answer, run.tool_results())
        if violations:
            run.record(StepKind.GUARDRAIL, category="output", violations=violations)
        run.final_answer = answer
        run.status = RunStatus.COMPLETED
        run.record(StepKind.FINAL_ANSWER, text=answer)
        log.info("run_completed", extra={"run_id": run.id})
        self.store.save(run)
        return run

    def _fail(self, run: RunState, error: str) -> RunState:
        refunded = [
            r["output"]["data"]["refund_id"]
            for r in run.tool_results()
            if r["tool"] == "issue_refund" and r["output"]["ok"]
        ]
        run.status = RunStatus.FAILED
        run.error = error
        run.final_answer = (
            f"The run stopped ({error}). Refund {refunded[0]} had already been issued."
            if refunded
            else f"The run stopped ({error}). No refund was issued."
        )
        run.record(StepKind.ERROR, message=error)
        log.error("run_failed", extra={"run_id": run.id, "error": error})
        self.store.save(run)
        return run
