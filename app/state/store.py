"""In-memory run store. Swap for Postgres/Redis in production; the interface stays the same."""

from __future__ import annotations

import threading
from pathlib import Path

from app.state.models import RunState, RunStatus


class RunNotFound(LookupError):
    pass


class InvalidTransition(RuntimeError):
    pass


class RunStore:
    def __init__(self, trace_dir: Path | None = None) -> None:
        self._runs: dict[str, RunState] = {}
        self._lock = threading.Lock()
        self._trace_dir = trace_dir

    def save(self, run: RunState) -> None:
        with self._lock:
            self._runs[run.id] = run.model_copy(deep=True)
        if self._trace_dir and (run.status.terminal or run.status == RunStatus.AWAITING_APPROVAL):
            self._trace_dir.mkdir(parents=True, exist_ok=True)
            (self._trace_dir / f"{run.id}.json").write_text(run.model_dump_json(indent=2))

    def get(self, run_id: str) -> RunState:
        with self._lock:
            if run_id not in self._runs:
                raise RunNotFound(run_id)
            return self._runs[run_id].model_copy(deep=True)

    def list(self, limit: int = 50) -> list[RunState]:
        with self._lock:
            runs = sorted(self._runs.values(), key=lambda r: r.created_at, reverse=True)
            return [r.model_copy(deep=True) for r in runs[:limit]]

    def claim_pending(self, run_id: str) -> RunState:
        """Atomically move AWAITING_APPROVAL -> RUNNING so a decision is applied exactly once."""
        with self._lock:
            run = self._runs.get(run_id)
            if run is None:
                raise RunNotFound(run_id)
            if run.status != RunStatus.AWAITING_APPROVAL:
                raise InvalidTransition(f"run is '{run.status}', not awaiting approval")
            run.status = RunStatus.RUNNING
            return run.model_copy(deep=True)
