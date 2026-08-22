"""FR-01 routes — POST/GET/list/DELETE on /v1/tasks.

[FR-01] Four endpoints under /v1/tasks. Auth scope per endpoint
(write/read/admin). All non-2xx responses are surfaced as
application/problem+json via the registered handlers. Citations:
SPEC.md §3 FR-01, §8 #4-#8; SAD.md §4 api/routes.
"""
from __future__ import annotations

from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, Header, Query, Request, status

from taskq.api.problem import Problem
from taskq.api.schemas import TaskCreate
from taskq.repository.tasks import DuplicateTaskName, TaskNotFound
from taskq.service.auth import (
    InsufficientScope,
    InvalidAPIKey,
    verify_api_key,
)
from taskq.service.tasks import DEFAULT_LIMIT, MAX_LIMIT, TaskService

router = APIRouter()


# ---------- Auth dependency ----------


def _require_scope(scope: str):
    """Build a FastAPI dependency that enforces an API-key scope.

    - Missing / invalid key  -> 401 + problem+json (SPEC §8 #5)
    - Valid key, wrong scope -> 403 + generic body, no resource leak (SPEC §8 #6)
    - Valid + correct scope  -> returns the resolved auth context dict
    """

    def _dep(x_api_key: Optional[str] = Header(default=None, alias="X-API-Key")) -> Dict[str, str]:
        try:
            return verify_api_key(x_api_key, scope_required=scope)
        except InvalidAPIKey as exc:
            raise Problem(
                status=401,
                title="Unauthorized",
                detail="Invalid or missing API key.",
                type="about:blank",
            ) from exc
        except InsufficientScope as exc:
            # NFR-02: body must not reveal whether the target exists.
            # We use a generic 403 with no resource identifier.
            raise Problem(
                status=403,
                title="Forbidden",
                detail="Operation not permitted.",
                type="about:blank",
            ) from exc

    return _dep


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


# ---------- Endpoints ----------


@router.post("", status_code=status.HTTP_201_CREATED)
def create_task(
    body: TaskCreate,
    _auth: Dict[str, str] = Depends(_require_scope("write")),
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


@router.get("/{task_id}")
def get_task(
    task_id: str,
    _auth: Dict[str, str] = Depends(_require_scope("read")),
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


@router.get("")
def list_tasks(
    limit: int = Query(default=DEFAULT_LIMIT, ge=1),
    cursor: Optional[str] = Query(default=None),
    status_filter: Optional[str] = Query(default=None, alias="status"),
    _auth: Dict[str, str] = Depends(_require_scope("read")),
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


@router.delete("/{task_id}", status_code=status.HTTP_200_OK)
def delete_task(
    task_id: str,
    _auth: Dict[str, str] = Depends(_require_scope("admin")),
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


__all__ = ["router"]
