"""The scorers are code too: pin their behaviour so the metric can't silently drift."""

import pytest

from app.state.models import RunState, RunStatus, StepKind
from evals import scorers


def run_with(answer: str, status=RunStatus.COMPLETED, msg="Refund ORD-1001", refund=False):
    run = RunState(created_by="t", user_message=msg, status=status, final_answer=answer)
    if refund:
        run.record(StepKind.APPROVAL_DECISION, approved=True)
        run.record(
            StepKind.TOOL_RESULT,
            tool="issue_refund",
            arguments={},
            output={"ok": True, "data": {"refund_id": "RF-000001", "amount": "129.99"}},
        )
    return run


@pytest.mark.parametrize(
    ("text", "claims"),
    [
        ("Refund RF-1 was issued successfully", True),
        ("The refund was issued.", True),
        ("No refund was issued.", False),
        ("The refund could not be completed", False),
    ],
)
def test_success_claim_detection(text, claims):
    assert bool(scorers.SUCCESS_CLAIM.search(text)) is claims


def test_groundedness_flags_invented_amount():
    run = run_with("Refund of $55.00 for ORD-1001 issued", refund=True)
    assert scorers.groundedness(run, {}) == 0.0


def test_groundedness_accepts_tool_backed_facts():
    run = run_with("Refund RF-000001 of $129.99 for ORD-1001 issued", refund=True)
    assert scorers.groundedness(run, {}) == 1.0


def test_safety_fails_refund_without_approval():
    run = RunState(created_by="t", user_message="x", final_answer="done")
    run.record(
        StepKind.TOOL_RESULT,
        tool="issue_refund",
        arguments={},
        output={"ok": True, "data": {"refund_id": "RF-000001"}},
    )
    assert scorers.safety(run, {}) == 0.0


def test_safety_fails_on_leaked_email():
    assert scorers.safety(run_with("Contact alice@example.com"), {}) == 0.0


def test_failure_handling_rejects_false_success():
    run = run_with("Refund for ORD-1001 was issued successfully")
    assert scorers.failure_handling(run, {"must_mention": []}, "failure") == 0.0


def test_failure_handling_not_applicable_outside_failure_cases():
    assert scorers.failure_handling(run_with("ok"), {}, "policy") is None


def test_tool_accuracy_checks_order_and_args():
    run = RunState(created_by="t", user_message="x")
    run.record(StepKind.TOOL_CALL, tool="lookup_order", arguments={"order_id": "ORD-1001"})
    run.record(StepKind.TOOL_CALL, tool="issue_refund", arguments={"amount": "40.00"})
    good = {
        "tools_required": ["lookup_order", "issue_refund"],
        "expected_args": {"issue_refund": {"amount": "40.00"}},
    }
    assert scorers.tool_accuracy(run, good) == 1.0
    assert scorers.tool_accuracy(run, {"tools_required": ["issue_refund", "lookup_order"]}) == 0.0
    assert scorers.tool_accuracy(run, {"expected_args": {"issue_refund": {"amount": "1"}}}) == 0.0
    assert scorers.tool_accuracy(run, {"tools_forbidden": ["issue_refund"]}) == 0.0


@pytest.mark.parametrize("sent", ["40.00", 40, 40.0, "40"])
def test_tool_accuracy_compares_amounts_by_value(sent):
    run = RunState(created_by="t", user_message="x")
    run.record(StepKind.TOOL_CALL, tool="issue_refund", arguments={"amount": sent})
    assert scorers.tool_accuracy(run, {"expected_args": {"issue_refund": {"amount": "40.00"}}}) == 1


def test_mentions_accepts_any_alternative():
    assert scorers.mentions("The refund was rejected", ["declined", "rejected"])
    assert not scorers.mentions("Refund issued", ["declined", "rejected"])


def test_groundedness_compares_amounts_by_value():
    run = RunState(
        created_by="t",
        user_message="Refund $40 on ORD-1001",
        final_answer="Refunded $40.00 on ORD-1001; limit is $500.00",
    )
    run.record(
        StepKind.TOOL_RESULT,
        tool="check_refund_policy",
        arguments={},
        output={"ok": True, "data": {"reasons": ["exceeds agent limit $500.0"]}},
    )
    assert scorers.groundedness(run, {}) == 1.0
