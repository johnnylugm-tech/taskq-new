"""FR-02 handlers — POST /v1/tasks/{id}/run + GET /v1/tasks/{id}/runs.

[FR-02] Two handlers covering the run lifecycle:

  * ``trigger_run`` (scope=write) — AC-2.1
      Returns HTTP 202 + ``{"run_id": "<uuid>"}`` immediately. The
      actual subprocess run is scheduled via FastAPI's
      ``BackgroundTasks`` (threadpool) so the handler never blocks on
      subprocess exit (NFR-03).

  * ``list_runs`` (scope=read) — AC-2.5
      Returns the task's run history ordered newest-first.

These are plain functions registered on the FastAPI app by
``taskq.api.app.create_app`` so the routes appear directly in
``app.routes`` (FastAPI 0.141 wraps ``include_router`` calls in an
``_IncludedRouter`` that hides prefixed paths from ``app.routes``,
which the FR-04 AC-4.3 audit walks).

[FR-04] Auth on every handler uses the SINGLE canonical
``taskq.api.deps.require_scope`` dependency (AC-4.3) — same dependency
as ``taskq.api.routes.tasks``. Scope hierarchy (AC-4.1) is enforced
inside ``taskq.service.auth.verify_api_key``; insufficient scope maps
to HTTP 403 + generic body via the shared dependency (AC-4.2,
NFR-02 / SPEC §8 #6).

Per-request repository accessors stay on the handler module so the
handlers can be registered independently (no cross-router coupling,
NFR-06).

Citations: SPEC.md §3 FR-02, §3 FR-04, §5.2, §8 #16; SAD.md §4
api/routes/runs; NFR-02 (no leak); NFR-03 (async correctness);
NFR-06 (layer contract).
"""
from __future__ import annotations

import uuid
from typing import Any, Dict

from fastapi import APIRouter, BackgroundTasks, Depends, Request

from taskq.api.deps import require_scope
from taskq.api.problem import Problem
from taskq.repository.results import TaskResultRepository
from taskq.repository.tasks import TaskNotFound, TaskRepository
from taskq.service.runner import TaskRunner

# Back-compat shim: FR-02 tests still import ``router`` and
# ``_require_scope`` from this module. The new direct-registration
# pattern in ``taskq.api.app.create_app`` does NOT include this
# router — the /v1/tasks/{id}/run + /runs handlers are registered
# directly on the FastAPI app so they surface as ``APIRoute``
# entries in ``app.routes`` for the FR-04 AC-4.3 audit.
router = APIRouter()


def _require_scope(scope: str):
    """FR-02 back-compat factory — delegates to ``taskq.api.deps``.

    FR-02 tests import this name to drive the 403 branch of the
    admin-required dependency directly. Returns the SAME callable
    shape as the new shared ``require_scope`` factory.
    """
    return require_scope(scope)


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
# Handlers
# ---------------------------------------------------------------------------


def trigger_run(
    task_id: str,
    background_tasks: BackgroundTasks,
    _auth: Dict[str, str] = Depends(require_scope("write")),
    tasks_repo: TaskRepository = Depends(_get_tasks_repo),
    results_repo: TaskResultRepository = Depends(_get_results_repo),
) -> Dict[str, Any]:
    """AC-2.1 — POST /v1/tasks/{id}/run returns 202 + run_id.

    The handler does NOT block on subprocess exit (NFR-03); the actual
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
        """Background-thread body: execute + persist outcome to the same row.

        The runner's ``terminal`` string (``done`` / ``failed`` / ``timeout``)
        is forwarded as the persisted ``status`` so AC-2.3's ``timeout``
        terminal state is preserved in ``task_results`` instead of being
        collapsed to ``failed`` by an exit-code heuristic.
        """
        result = TaskRunner().run(task_id=task_id, command=command)
        results_repo.update_result(
            run_id=run_id,
            exit_code=result["exit_code"],
            stdout_tail=result["stdout_tail"],
            stderr_tail=result["stderr_tail"],
            duration_ms=result["duration_ms"],
            finished_at=result["finished_at"],
            status=result["terminal"],
        )

    background_tasks.add_task(_run_and_persist)
    return {"run_id": run_id}


def list_runs(
    task_id: str,
    _auth: Dict[str, str] = Depends(require_scope("read")),
    results_repo: TaskResultRepository = Depends(_get_results_repo),
) -> Dict[str, Any]:
    """AC-2.5 — GET /v1/tasks/{id}/runs returns history newest-first.

    NFR-06: the handler reaches ``task_results`` ONLY through the
    repository layer (no SQL in the handler).
    """
    rows = results_repo.list_results_for_task(task_id=task_id)
    return {"runs": rows}


__all__ = [
    "trigger_run",
    "list_runs",
    "_get_tasks_repo",
    "_get_results_repo",
]