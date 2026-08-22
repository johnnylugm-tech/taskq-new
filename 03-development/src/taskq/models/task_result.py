"""TaskResult ORM model.

[FR-01] Result rows belonging to a Task. The admin DELETE cascade in
AC-1.10 / FR-01 must remove both task + result rows in one transaction.
Citations: SPEC.md §3 FR-01 (cascade rule); SAD.md §4 Models layer.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import DateTime, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from taskq.models.base import Base


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _new_uuid() -> str:
    return str(uuid.uuid4())


class TaskResult(Base):
    """A single execution result row attached to a Task.

    Columns:
        id          — UUID string PK
        task_id     — FK to tasks.id
        command     — command that produced this result (echoed for audit)
        status      — execution status string
        created_at  — UTC timestamp
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
