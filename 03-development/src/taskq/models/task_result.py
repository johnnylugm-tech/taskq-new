"""TaskResult ORM model.

[FR-01] Result rows belonging to a Task. The admin DELETE cascade in
AC-1.10 / FR-01 must remove both task + result rows in one transaction.

[FR-02] v3-schema columns: ``exit_code``, ``stdout_tail``, ``stderr_tail``,
``duration_ms``, ``finished_at`` (SPEC.md §3 FR-02, §5.2). All nullable so
the FR-01 seeding path (``TaskRepository.create_task_result``) still
satisfies NOT NULL only on the legacy columns.

Citations: SPEC.md §3 FR-01, §3 FR-02, §5.2; SAD.md §4 Models layer.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import DateTime, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from taskq.models.base import Base


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _new_uuid() -> str:
    return str(uuid.uuid4())


class TaskResult(Base):
    """A single execution result row attached to a Task.

    Columns:
        id          — UUID string PK (also used as ``run_id`` in FR-02)
        task_id     — FK to tasks.id
        command     — command that produced this result (echoed for audit)
        status      — execution status string ("pending"/"done"/"failed"/"timeout")
        created_at  — UTC timestamp set at row insert time
        exit_code   — FR-02 v3 schema: process exit code (NULL while pending)
        stdout_tail — FR-02 v3 schema: captured stdout (tail-capped)
        stderr_tail — FR-02 v3 schema: captured stderr (tail-capped)
        duration_ms — FR-02 v3 schema: wall-clock duration in ms
        finished_at — FR-02 v3 schema: UTC timestamp at process exit
    """

    __tablename__ = "task_results"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_new_uuid)
    task_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("tasks.id"), nullable=False, index=True
    )
    command: Mapped[str] = mapped_column(String(2000), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="ok")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utc_now
    )

    # ---- FR-02 / v3 schema columns (AC-2.4) ----
    # Nullable (with sensible defaults on the text columns) so FR-01's
    # `create_task_result(task_id, command)` path stays valid.
    exit_code: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    stdout_tail: Mapped[str] = mapped_column(
        String(8000), nullable=False, default=""
    )
    stderr_tail: Mapped[str] = mapped_column(
        String(8000), nullable=False, default=""
    )
    duration_ms: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    finished_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


__all__ = ["TaskResult"]
