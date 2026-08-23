"""Task ORM model.

[FR-01] Task resource row; `name` carries a unique constraint enforced at the
persistence layer (NFR-02). Citations: SPEC.md §3 FR-01, §8 #8 (uniqueness
guard); SAD.md §4 Models layer.
"""
from __future__ import annotations

# pragma: no error-handling

import uuid
from datetime import datetime, timezone
from typing import List, TYPE_CHECKING

from sqlalchemy import DateTime, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from taskq.models.base import Base

if TYPE_CHECKING:
    from taskq.models.task_result import TaskResult


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _new_uuid() -> str:
    return str(uuid.uuid4())


class Task(Base):
    """A Task resource — the unit of CRUD in FR-01.

    Columns:
        id          — UUID string PK (NFR-06 — no SQL concat, ORM-managed)
        name        — human label, unique (NFR-02)
        command     — shell command (validated by API layer per SPEC §7)
        status      — lifecycle marker (e.g. "queued")
        created_at  — UTC timestamp
    """

    __tablename__ = "tasks"
    __table_args__ = (UniqueConstraint("name", name="uq_tasks_name"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_new_uuid)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    command: Mapped[str] = mapped_column(String(2000), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="queued")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utc_now
    )

    # ---- FR-06 / AC-6.4 — eager-loadable relationship ----
    # ``selectinload(Task.results)`` / ``joinedload(Task.results)`` on the
    # list endpoint prevents the N+1 pattern (NFR-01, §8 #14). The
    # backref exposes ``TaskResult.task`` symmetrically; ORM-only, no
    # schema impact. Cascade is intentionally NOT set here — the
    # AC-1.10 cascade test exercises the SQL-level cascade path, not
    # the ORM-level one (FR-01).
    results: Mapped[List["TaskResult"]] = relationship(
        "TaskResult",
        backref="task",
        lazy="select",
    )
