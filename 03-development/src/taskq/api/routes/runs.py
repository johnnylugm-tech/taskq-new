"""FR-02 routes — POST /v1/tasks/{id}/run + GET /v1/tasks/{id}/runs.

[FR-02] Two endpoints mounted under the same ``/v1/tasks`` prefix as
the FR-01 tasks router:

  * ``POST /{task_id}/run``  (scope=write) — AC-2.1
      Returns HTTP 202 + ``{"run_id": "<uuid>"}`` immediately. The
      actual subprocess run is scheduled via FastAPI's
      ``BackgroundTasks`` (threadpool) so the route never blocks on
      subprocess exit (NFR-03).

  * ``GET /{task_id}/runs`` (scope=read) — AC-2.5
      Returns the task's run history ordered newest-first.

Auth, problem+json error envelope, and the per-request repository
accessors follow the same pattern as ``taskq.api.routes.tasks`` so the
two routers stay independent (no cross-router coupling) per the SAD.

Citations: SPEC.md §3 FR-02, §5.2, §8 #16; SAD.md §4 api/routes/runs;
NFR-02 (no leak); NFR-03 (async correctness); NFR-06 (layer contract).
"""
from __future__ import annotations

import uuid
from typing import Any, Dict, Optional

from fastapi import APIRouter, BackgroundTasks, Depends, Header, Request, status

from taskq.api.problem import Problem
from taskq.repository.results import TaskResultRepository
from taskq.repository.tasks import TaskNotFound, TaskRepository
from taskq.service.auth import InsufficientScope, InvalidAPIKey, verify_api_key
from taskq.service.runner import TaskRunner

router = APIRouter()


# ---------------------------------------------------------------------------
# Auth dependency (mirrors tasks.py so the routers stay independent)
# ---------------------------------------------------------------------------


def _require_scope(scope: str):
    """Build a FastAPI dependency that enforces an API-key scope.

    - Missing / invalid key  -> 401 + problem+json (SPEC §8 #5)
    - Valid key, wrong scope -> 403 + generic body (SPEC §8 #6)
    - Valid + correct scope  -> returns the resolved auth context dict
    """

    def _dep(
        x_api_key: Optional[str] = Header(default=None, alias="X-API-Key"),
    ) -> Dict[str, str]:
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
            raise Problem(
                status=403,
                title="Forbidden",
                detail="Operation not permitted.",
                type="about:blank",
            ) from exc

    return _dep


# ---------------------------------------------------------------------------
# Per-request repository accessors (cached on app.state, shared with FR-01)
# ---------------------------------------------------------------------------


def _get_tasks_repo(request: Request) -> TaskRepository:
    repo = getattr(request.app.state, "task_repository", None)
    if repo is None:
        repo = TaskRepository()
        request.app.state.task_repository = repo
    return repo


def _get_results_repo(request: Request) -> TaskResultRepository:
    repo = getattr(request.app.state, "results_repository", None)
    if repo is None:
        repo = TaskResultRepository()
        request.app.state.results_repository = repo
    return repo


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.post("/{task_id}/run", status_code=status.HTTP_202_ACCEPTED)
def trigger_run(
    task_id: str,
    background_tasks: BackgroundTasks,
    _auth: Dict[str, str] = Depends(_require_scope("write")),
    tasks_repo: TaskRepository = Depends(_get_tasks_repo),
    results_repo: TaskResultRepository = Depends(_get_results_repo),
) -> Dict[str, Any]:
    """AC-2.1 — POST /v1/tasks/{id}/run returns 202 + run_id.

    The route does NOT block on subprocess exit (NFR-03); the actual
    execution is scheduled via FastAPI's BackgroundTasks, which runs in
    the threadpool AFTER the 202 response is sent. The HTTP-layer
    contract is preserved by inserting a pending ``task_results`` row
    at trigger time so the GET endpoint can list runs in trigger order
    even before the subprocess finishes.
    """
    try:
        task = tasks_repo.get(task_id)
    except TaskNotFound as exc:
        raise Problem(
            status=404,
            title="Not found",
            detail="Resource not found.",
            type="about:blank",
        ) from exc

    run_id = str(uuid.uuid4())
    command = task.get("command", "")
    # Insert a pending row at trigger time so the GET endpoint can
    # list runs newest-first by ``created_at`` (set now) even when the
    # subprocess completes out of order.
    results_repo.create_pending_result(
        task_id=task_id, run_id=run_id, command=command
    )

    def _run_and_persist() -> None:
        """Background-thread body: execute + persist outcome to the same row."""
        result = TaskRunner().run(task_id=task_id, command=command)
        results_repo.update_result(
            run_id=run_id,
            exit_code=result["exit_code"],
            stdout_tail=result["stdout_tail"],
            stderr_tail=result["stderr_tail"],
            duration_ms=result["duration_ms"],
            finished_at=result["finished_at"],
        )

    background_tasks.add_task(_run_and_persist)
    return {"run_id": run_id}


@router.get("/{task_id}/runs")
def list_runs(
    task_id: str,
    _auth: Dict[str, str] = Depends(_require_scope("read")),
    results_repo: TaskResultRepository = Depends(_get_results_repo),
) -> Dict[str, Any]:
    """AC-2.5 — GET /v1/tasks/{id}/runs returns history newest-first.

    NFR-06: the route reaches ``task_results`` ONLY through the
    repository layer (no SQL in the route).
    """
    rows = results_repo.list_results_for_task(task_id=task_id)
    return {"runs": rows}


__all__ = ["router"]
