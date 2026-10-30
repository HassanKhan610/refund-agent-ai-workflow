from concurrent.futures import ThreadPoolExecutor

import pytest

from app.state.models import RunState, RunStatus, StepKind
from app.state.store import InvalidTransition, RunNotFound, RunStore


def test_save_and_get_are_isolated_copies():
    store = RunStore()
    run = RunState(created_by="requester", user_message="hi")
    store.save(run)
    run.final_answer = "mutated after save"
    assert store.get(run.id).final_answer is None


def test_missing_run():
    with pytest.raises(RunNotFound):
        RunStore().get("run_missing")


def test_claim_pending_only_once_under_concurrency():
    store = RunStore()
    run = RunState(created_by="r", user_message="x", status=RunStatus.AWAITING_APPROVAL)
    store.save(run)

    def claim(_):
        try:
            store.claim_pending(run.id)
            return True
        except InvalidTransition:
            return False

    with ThreadPoolExecutor(8) as pool:
        assert sum(pool.map(claim, range(8))) == 1


def test_trace_written_for_terminal_runs(tmp_path):
    store = RunStore(trace_dir=tmp_path)
    run = RunState(created_by="r", user_message="x", status=RunStatus.COMPLETED)
    run.record(StepKind.FINAL_ANSWER, text="done")
    store.save(run)
    assert (tmp_path / f"{run.id}.json").exists()
