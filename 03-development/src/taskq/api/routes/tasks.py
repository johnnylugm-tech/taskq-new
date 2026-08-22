"""FR-01 handlers — POST/GET/list/DELETE on /v1/tasks.

[FR-01] Four handlers covering /v1/tasks. Each handler is a plain
function; registration on the FastAPI app happens in
``taskq.api.app.create_app`` so the routes appear directly in
``app.routes`` (FastAPI 0.141 wraps ``include_router`` calls in an
``_IncludedRouter`` that hides the prefixed paths from ``app.routes``,
which the FR-04 AC-4.3 audit walks).

[FR-04] Every handler uses the SINGLE canonical
``taskq.api.deps.require_scope`` dependency (AC-4.3). The scope
hierarchy ``read < write < admin`` is enforced inside
``taskq.service.auth.verify_api_key`` (AC-4.1); insufficient scope
maps to HTTP 403 + generic body via the shared dependency (AC-4.2,
NFR-02 / SPEC §8 #6).

Citations: SPEC.md §3 FR-01, §3 FR-04, §8 #4-#8; SAD.md §4 api/routes.
"""
from __future__ import annotations

from typing import Any, Dict, Optional

from fastapi import Depends, Query, Request

from taskq.api.deps import require_scope
from taskq.api.problem import Problem
from taskq.api.schemas import TaskCreate
from taskq.repository.tasks import DuplicateTaskName, TaskNotFound
from taskq.service.tasks import DEFAULT_LIMIT, MAX_LIMIT, TaskService


# ---------- Service accessor (per-request) ----------


def _get_service(request: Request) -> TaskService:
    """Return the process-shared TaskService.

    A real deployment would scope a service per request; for GREEN/TDD we
    rely on a shared instance plus the test-time DB reset.
    """
    svc = getattr(request.app.state, "task_service", None)
    if svc is None:
        svc = TaskService()
        request.app.state.task_service = svc
    return svc


# ---------- Handlers ----------


def create_task(
    body: TaskCreate,
    _auth: Dict[str, str] = Depends(require_scope("write")),
    service: TaskService = Depends(_get_service),
) -> Dict[str, Any]:
    """AC-1.1 / AC-1.4: create a task; 409 on name collision."""
    try:
        created = service.create_task(name=body.name, command=body.command)
    except DuplicateTaskName as exc:
        raise Problem(
            status=409,
            title="Conflict",
            detail="Task name already exists.",
            type="about:blank",
        ) from exc
    return created


def get_task(
    task_id: str,
    _auth: Dict[str, str] = Depends(require_scope("read")),
    service: TaskService = Depends(_get_service),
) -> Dict[str, Any]:
    """AC-1.5 / AC-1.6: get a single task; 404 if unknown."""
    try:
        return service.get_task(task_id=task_id)
    except TaskNotFound as exc:
        raise Problem(
            status=404,
            title="Not found",
            detail="Resource not found.",
            type="about:blank",
        ) from exc


def list_tasks(
    limit: int = Query(default=DEFAULT_LIMIT, ge=1),
    cursor: Optional[str] = Query(default=None),
    status_filter: Optional[str] = Query(default=None, alias="status"),
    _auth: Dict[str, str] = Depends(require_scope("read")),
    service: TaskService = Depends(_get_service),
) -> Dict[str, Any]:
    """AC-1.7 / AC-1.8: cursor-paginated list; 422 when limit > 200."""
    if limit > MAX_LIMIT:
        raise Problem(
            status=422,
            title="Validation failed",
            detail=f"limit must be <= {MAX_LIMIT}",
            type="about:blank",
        )
    return service.list_tasks(limit=limit, cursor=cursor, status=status_filter)


def delete_task(
    task_id: str,
    _auth: Dict[str, str] = Depends(require_scope("admin")),
    service: TaskService = Depends(_get_service),
) -> Dict[str, Any]:
    """AC-1.9 / AC-1.10: admin-only delete with cascade in one transaction."""
    try:
        service.delete_task(task_id=task_id)
    except TaskNotFound as exc:
        raise Problem(
            status=404,
            title="Not found",
            detail="Resource not found.",
            type="about:blank",
        ) from exc
    return {"deleted": True, "id": task_id}


__all__ = [
    "create_task",
    "get_task",
    "list_tasks",
    "delete_task",
    "_get_service",
]