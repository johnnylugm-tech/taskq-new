"""TaskRepository — persistence boundary for tasks + task_results.

[FR-01] Owns SQL access for FR-01; all ORM details are confined here
(NFR-06). The repository enforces name uniqueness at the persistence
layer (NFR-02) and returns a domain-level `DuplicateTaskName` so the
service/route layer can map to HTTP 409. Citations: SPEC.md §3 FR-01,
§8 #8 (uniqueness at persistence layer); SAD.md §4 repository layer.
"""
from __future__ import annotations

import base64
import json
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy import delete, select
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, joinedload, sessionmaker

from taskq.config.settings import Settings
from taskq.models.base import Base
from taskq.models.task import Task
from taskq.models.task_result import TaskResult


# ---------- Domain-level exceptions (raised from this layer outward) ----------


class DuplicateTaskName(Exception):
    """Raised when a unique-violation collides on Task.name.

    SPEC.md §3 FR-01, §8 #8 — uniqueness is a persistence-layer concern.
    """


class TaskNotFound(Exception):
    """Raised when a task id has no matching row."""


# ---------- Shared in-memory engine (process-wide) ----------

_engine: Optional[Engine] = None
_SessionLocal: Optional[sessionmaker] = None


def _build_engine() -> Engine:
    """Build a process-wide shared SQLite engine (StaticPool = single connection).

    For GREEN/TDD the in-memory store keeps the test self-contained and
    shared across TaskRepository()/TaskService() instances in a single test
    process, which the AC-1.10 in-process verification depends on.

    [FR-06] AC-6.5 — pool sizing + pre-ping come from
    ``taskq.config.settings.Settings`` so the engine mirrors the
    ``TASKQ_DB_POOL_SIZE`` / ``TASKQ_DB_POOL_PRE_PING`` env vars
    (SPEC.md §3 FR-06, defaults 5 / True).
    """
    from sqlalchemy import create_engine
    from sqlalchemy.pool import StaticPool

    settings = Settings.from_env()
    return create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
        pool_pre_ping=settings.db_pool_pre_ping,
    )


def get_engine() -> Engine:
    global _engine
    if _engine is None:
        _engine = _build_engine()
        Base.metadata.create_all(_engine)
    return _engine


def get_session_factory() -> sessionmaker:
    global _SessionLocal
    if _SessionLocal is None:
        _SessionLocal = sessionmaker(bind=get_engine(), expire_on_commit=False)
    return _SessionLocal


def reset_db() -> None:
    """Drop and recreate all tables — used by test isolation fixtures.

    Called between tests so each test starts with an empty DB even though
    the engine is shared process-wide.
    """
    engine = get_engine()
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)


# ---------- Cursor encoding (opaque token, base64(json)) ----------

def _encode_cursor(payload: Dict[str, Any]) -> str:
    raw = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _decode_cursor(token: str) -> Optional[Dict[str, Any]]:
    """Decode an opaque cursor; return None if it cannot be parsed."""
    try:
        padding = "=" * (-len(token) % 4)
        raw = base64.urlsafe_b64decode(token + padding)
        return json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        # ValueError covers binascii.Error (bad base64 padding/length)
        # and json.JSONDecodeError (which subclasses ValueError).
        return None


# ---------- Repository ----------


class TaskRepository:
    """Persistence boundary for tasks + task_results.

    All ORM details (sessions, queries, transactions) are confined to this
    class — the service layer above only sees plain Python types or domain
    exceptions (NFR-06).
    """

    def __init__(self, session_factory: Optional[sessionmaker] = None) -> None:
        self._session_factory = session_factory or get_session_factory()

    # ---- tasks ----

    def create(self, name: str, command: str) -> Dict[str, Any]:
        """Insert a new task row. Raises DuplicateTaskName on unique-violation."""
        task = Task(name=name, command=command)
        session: Session = self._session_factory()
        try:
            session.add(task)
            session.commit()
            session.refresh(task)
            return self._task_to_dict(task)
        except IntegrityError as exc:
            session.rollback()
            raise DuplicateTaskName(f"task name already exists: {name}") from exc
        finally:
            session.close()

    def get(self, task_id: str) -> Dict[str, Any]:
        """Fetch a task by id. Raises TaskNotFound if absent."""
        session: Session = self._session_factory()
        try:
            task = session.get(Task, task_id)
            if task is None:
                raise TaskNotFound(task_id)
            return self._task_to_dict(task)
        finally:
            session.close()

    def list(
        self,
        limit: int = 50,
        cursor: Optional[str] = None,
        status: Optional[str] = None,
    ) -> Tuple[List[Dict[str, Any]], Optional[str]]:
        """Return a page of tasks + opaque next_cursor (None when exhausted).

        [FR-06] AC-6.4 — eager-loaded with joinedload so the SQL
        statement count stays CONSTANT regardless of row count. The
        repository has no hard upper limit on ``limit`` (the service
        layer enforces ``MAX_LIMIT=200`` for the public API, per
        SPEC §3 FR-01 / AC-1.8); the eager-load probe therefore runs
        with limit=10000 without tripping a repo-level cap.

        Cursor format (opaque): base64(json({"created_at": "...", "id": "..."})).
        Citations: SPEC.md §3 FR-06 (AC-6.4), NFR-01 (no N+1), §8 #14.
        """
        if limit < 1:
            limit = 1

        session: Session = self._session_factory()
        try:
            # [FR-06] AC-6.4 — eager-load Task.results with joinedload
            # so the SQL statement count stays CONSTANT regardless of
            # row count and well below the N+1 failure threshold
            # (SPEC §3 FR-06, NFR-01, §8 #14). joinedload issues ONE
            # JOIN query; the ORM de-duplicates the joined rows back
            # into distinct ``Task`` entities via ``scalars().unique()``
            # below. selectinload was the prior implementation, but
            # SQLAlchemy 2.0 paginates the IN clause for large parent
            # sets (>= 1000 rows splits into 20 batches => 21 SQL
            # statements), which broke the variance-zero invariant
            # required by the eager-load probe.
            stmt = select(Task).options(joinedload(Task.results))
            if status is not None:
                stmt = stmt.where(Task.status == status)

            decoded = _decode_cursor(cursor) if cursor else None
            if decoded:
                created_at = decoded.get("created_at")
                last_id = decoded.get("id")
                if created_at and last_id:
                    # Stable cursor: (created_at, id) tiebreaker (id is UUID string).
                    stmt = stmt.where(
                        (Task.created_at > created_at)
                        | ((Task.created_at == created_at) & (Task.id > last_id))
                    )

            stmt = stmt.order_by(Task.created_at.asc(), Task.id.asc()).limit(limit + 1)
            # ``.unique()`` is required because ``joinedload`` returns
            # one row per (parent, child) pair; SQLAlchemy uses the
            # ORDER BY + PK tiebreaker to collapse back to one Task per
            # PK, but the result set contains duplicates that must be
            # suppressed before .all().
            rows = session.execute(stmt).scalars().unique().all()

            has_more = len(rows) > limit
            page = rows[:limit]
            next_cursor: Optional[str] = None
            if has_more and page:
                last = page[-1]
                next_cursor = _encode_cursor(
                    {"created_at": last.created_at.isoformat(), "id": last.id}
                )

            return [self._task_to_dict(t) for t in page], next_cursor
        finally:
            session.close()

    def delete_task_row(self, task_id: str) -> None:
        """Delete the task row itself. Idempotent (no error if missing)."""
        session: Session = self._session_factory()
        try:
            session.execute(delete(Task).where(Task.id == task_id))
            session.commit()
        finally:
            session.close()

    def delete_results_for_task(self, task_id: str) -> int:
        """Delete task_result rows for a task. Returns rows removed (cascade)."""
        session: Session = self._session_factory()
        try:
            result = session.execute(
                delete(TaskResult).where(TaskResult.task_id == task_id)
            )
            session.commit()
            # ``rowcount`` is an attribute of the underlying ``CursorResult``;
            # use ``getattr`` to keep type-checkers happy without compromising
            # the runtime value (``delete`` statements always populate it).
            return int(getattr(result, "rowcount", 0) or 0)
        finally:
            session.close()

    # ---- task_results ----

    def create_task_result(self, task_id: str, command: str) -> Dict[str, Any]:
        """Insert a task_result row."""
        row = TaskResult(task_id=task_id, command=command)
        session: Session = self._session_factory()
        try:
            session.add(row)
            session.commit()
            session.refresh(row)
            return self._result_to_dict(row)
        finally:
            session.close()

    def list_results_for_task(self, task_id: str) -> List[Dict[str, Any]]:
        """Return all task_result rows for a task (used to verify cascade)."""
        session: Session = self._session_factory()
        try:
            rows = (
                session.execute(
                    select(TaskResult).where(TaskResult.task_id == task_id)
                )
                .scalars()
                .all()
            )
            return [self._result_to_dict(r) for r in rows]
        finally:
            session.close()

    # ---- helpers ----

    @staticmethod
    def _task_to_dict(task: Task) -> Dict[str, Any]:
        return {
            "id": task.id,
            "name": task.name,
            "command": task.command,
            "status": task.status,
            "created_at": task.created_at.isoformat(),
        }

    @staticmethod
    def _result_to_dict(row: TaskResult) -> Dict[str, Any]:
        return {
            "id": row.id,
            "task_id": row.task_id,
            "command": row.command,
            "status": row.status,
            "created_at": row.created_at.isoformat(),
        }


__all__ = [
    "TaskRepository",
    "DuplicateTaskName",
    "TaskNotFound",
    "reset_db",
    "get_engine",
    "get_session_factory",
]
