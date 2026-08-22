"""TaskResultRepository — FR-02 / v3-schema persistence boundary.

[FR-02] Persistence boundary for ``task_results`` rows carrying the
v3-schema columns (``exit_code`` / ``stdout_tail`` / ``stderr_tail`` /
``duration_ms`` / ``finished_at``). All ORM access is confined to this
layer (NFR-06) — service / route callers see only plain dicts or domain
exceptions.

Two write paths share this repository:

  * ``record_result`` — single-insert with the 5 columns populated. Used
    by the AC-2.4 in-process test and as a fire-and-forget persistence
    option for the runner.
  * ``create_pending_result`` + ``update_result`` — POST ``/v1/tasks/{id}/run``
    inserts a pending row at trigger time (so the GET endpoint can list
    runs newest-first by ``created_at`` even before the subprocess
    completes), and the background runner UPDATEs the same row on exit.

Citations: SPEC.md §3 FR-02, §5.2; SAD.md §4 repository/results; NFR-06.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from taskq.models.task_result import TaskResult
from taskq.repository.tasks import get_session_factory


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _parse_finished_at(value: Any) -> datetime:
    """Coerce an ISO-8601 string (or datetime) into a tz-aware datetime.

    Accepts the trailing ``Z`` suffix (RFC 3339 subset) as UTC. Used by
    both ``record_result`` and ``update_result`` so the AC-2.4 test can
    pass ``"2026-08-22T00:00:00Z"`` and the runner can pass its own
    ``datetime.now(timezone.utc).isoformat()`` output.
    """
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value
    if isinstance(value, str):
        # Tolerate the "Z" suffix on UTC timestamps.
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    raise ValueError(f"unsupported finished_at value: {value!r}")


def _terminal_status(exit_code: Optional[int]) -> str:
    """Map a process exit code to a row-level ``status`` string."""
    if exit_code == 0:
        return "done"
    return "failed"


# ---------------------------------------------------------------------------
# repository
# ---------------------------------------------------------------------------


class TaskResultRepository:
    """Persistence boundary for FR-02 ``task_results`` rows.

    The engine / session factory is shared with ``TaskRepository``
    (``taskq.repository.tasks``), so reads / writes across the two
    repositories observe the same SQLite database.
    """

    def __init__(self, session_factory: Optional[Any] = None) -> None:
        self._session_factory = session_factory or get_session_factory()

    # ---- writes ----

    def record_result(
        self,
        task_id: str,
        command: str,
        exit_code: int,
        stdout_tail: str,
        stderr_tail: str,
        duration_ms: int,
        finished_at: Any,
    ) -> Dict[str, Any]:
        """Insert a row populated with the v3-schema 5 columns.

        AC-2.4: the returned dict MUST contain ``exit_code``,
        ``stdout_tail``, ``stderr_tail``, ``duration_ms`` and
        ``finished_at`` (the FR-07 round-trip is not byte-identical
        without them).
        """
        finished_dt = _parse_finished_at(finished_at)
        row = TaskResult(
            task_id=task_id,
            command=command,
            status=_terminal_status(exit_code),
            exit_code=exit_code,
            stdout_tail=stdout_tail or "",
            stderr_tail=stderr_tail or "",
            duration_ms=duration_ms,
            finished_at=finished_dt,
        )
        session: Session = self._session_factory()
        try:
            session.add(row)
            session.commit()
            session.refresh(row)
            return self._to_dict(row)
        finally:
            session.close()

    def create_pending_result(
        self,
        task_id: str,
        run_id: str,
        command: str,
    ) -> Dict[str, Any]:
        """Insert a ``pending`` row at run-trigger time.

        POST ``/v1/tasks/{id}/run`` calls this BEFORE scheduling the
        background subprocess; the row carries ``run_id`` as its PK so
        the runner can UPDATE the same row on completion (no second
        insert, no duplicate ordering).
        """
        row = TaskResult(
            id=run_id,
            task_id=task_id,
            command=command,
            status="pending",
        )
        session: Session = self._session_factory()
        try:
            session.add(row)
            session.commit()
            return self._to_dict(row)
        finally:
            session.close()

    def update_result(
        self,
        run_id: str,
        exit_code: Optional[int],
        stdout_tail: str,
        stderr_tail: str,
        duration_ms: Optional[int],
        finished_at: Any,
    ) -> Dict[str, Any]:
        """Update the pending row (created by ``create_pending_result``) with the outcome.

        Used by the route's background task: the subprocess finished, the
        captured tail / exit code / duration are now known.
        """
        finished_dt = _parse_finished_at(finished_at)
        session: Session = self._session_factory()
        try:
            session.execute(
                update(TaskResult)
                .where(TaskResult.id == run_id)
                .values(
                    exit_code=exit_code,
                    stdout_tail=stdout_tail or "",
                    stderr_tail=stderr_tail or "",
                    duration_ms=duration_ms,
                    finished_at=finished_dt,
                    status=_terminal_status(exit_code),
                )
            )
            session.commit()
            row = session.get(TaskResult, run_id)
            if row is None:
                raise ValueError(f"task_result row not found: {run_id}")
            return self._to_dict(row)
        finally:
            session.close()

    # ---- reads ----

    def list_results_for_task(self, task_id: str) -> List[Dict[str, Any]]:
        """Return ``task_results`` rows for a task, newest-first.

        Ordering: ``created_at DESC, id DESC``. ``created_at`` is set at
        run-trigger time (POST ``/run``), so insertion order is preserved
        even when subprocesses complete out of order.
        """
        session: Session = self._session_factory()
        try:
            rows = (
                session.execute(
                    select(TaskResult)
                    .where(TaskResult.task_id == task_id)
                    .order_by(TaskResult.created_at.desc(), TaskResult.id.desc())
                )
                .scalars()
                .all()
            )
            return [self._to_dict(r) for r in rows]
        finally:
            session.close()

    # ---- helpers ----

    @staticmethod
    def _to_dict(row: TaskResult) -> Dict[str, Any]:
        return {
            "id": row.id,
            "run_id": row.id,
            "task_id": row.task_id,
            "command": row.command,
            "status": row.status,
            "created_at": row.created_at.isoformat(),
            "exit_code": row.exit_code,
            "stdout_tail": row.stdout_tail,
            "stderr_tail": row.stderr_tail,
            "duration_ms": row.duration_ms,
            "finished_at": row.finished_at.isoformat() if row.finished_at else None,
        }


__all__ = ["TaskResultRepository"]
