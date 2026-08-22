"""FastAPI app factory — create_app().

[FR-01] Returns a FastAPI instance with the FR-01 routes mounted and the
RFC 7807 problem+json exception handlers registered.

[FR-02] Additionally mounts the FR-02 run handlers under the same
``/v1/tasks`` prefix so POST ``/{task_id}/run`` and GET ``/{task_id}/runs``
resolve.

[FR-03] Adds unauthenticated ``/healthz`` and ``/readyz`` endpoints at
the root prefix (SPEC.md §3 FR-03 AC-3.6, FR-09). These are wired
directly on the app (no router, no auth dependency) so that health
checks succeed even when the api_keys table is unreachable.

[FR-04] All /v1 handlers are registered directly on the app via
``app.add_api_route`` rather than through ``app.include_router`` so
the routes appear as plain ``APIRoute`` entries in ``app.routes``.
FastAPI 0.141 wraps ``include_router`` calls in an ``_IncludedRouter``
that hides prefixed paths from ``app.routes`` — the FR-04 AC-4.3 audit
walks ``app.routes`` looking for paths starting with ``/v1/``, so
direct registration is the only way to make that audit see the
registered handlers. The single canonical
``taskq.api.deps.require_scope`` dependency is wired into every
/handler so the single-dep invariant (AC-4.3) holds.

The test fixture calls ``create_app()`` per test to get a fresh ASGI
app, while the underlying in-memory DB is reset between tests by the
conftest fixture (see 03-development/conftest.py).

Citations: SPEC.md §3 FR-01, §3 FR-02, §3 FR-03 (AC-3.6), §3 FR-04,
FR-09; NFR-10 (integration coverage via ASGITransport); SAD.md §4
api/app.
"""
from __future__ import annotations

from typing import Callable, List, Tuple

from fastapi import FastAPI

from taskq.api.handlers import register_exception_handlers
from taskq.api.routes.runs import list_runs, trigger_run
from taskq.api.routes.tasks import (
    create_task,
    delete_task,
    get_task,
    list_tasks,
)


# (path, http_methods, success_status_code, handler). Registered
# directly via ``app.add_api_route`` (NOT ``include_router``) so the
# routes surface in ``app.routes`` as ``APIRoute`` entries; the
# FR-04 AC-4.3 audit walks ``app.routes`` for paths starting with
# ``/v1/`` — see module docstring for the FastAPI 0.141 routing
# wrapper rationale.
_V1_ROUTES: Tuple[Tuple[str, List[str], int, Callable], ...] = (
    ("/v1/tasks",                ["POST"],   201, create_task),
    ("/v1/tasks/{task_id}",      ["GET"],    200, get_task),
    ("/v1/tasks",                ["GET"],    200, list_tasks),
    ("/v1/tasks/{task_id}",      ["DELETE"], 200, delete_task),
    ("/v1/tasks/{task_id}/run",  ["POST"],   202, trigger_run),
    ("/v1/tasks/{task_id}/runs", ["GET"],    200, list_runs),
)


def create_app() -> FastAPI:
    """Build a fresh FastAPI app for FR-01 + FR-02 + FR-03 + FR-04."""
    app = FastAPI(
        title="taskq API",
        version="0.1.0",
        # Disable the default redirect_slashes behaviour so that 422
        # validation responses aren't turned into 307 redirects (which
        # would break the problem+json contract from SPEC §10 / FR-10).
        redirect_slashes=False,
    )
    register_exception_handlers(app)

    # ---- FR-01 / FR-02 / FR-04: register /v1 handlers ----
    for path, methods, status_code, handler in _V1_ROUTES:
        app.add_api_route(
            path,
            handler,
            methods=methods,
            status_code=status_code,
        )

    # ---- FR-03 AC-3.6 / FR-09: unauthenticated health endpoints ----
    # Mounted directly on the app (no router prefix, no auth dependency)
    # so that /healthz and /readyz respond 200 regardless of whether
    # the api_keys table is reachable. SPEC §3 FR-09 / AC-3.6.
    @app.get("/healthz", include_in_schema=False)
    def healthz() -> dict:
        """Liveness probe — returns 200 with no body work."""
        return {"status": "ok"}

    @app.get("/readyz", include_in_schema=False)
    def readyz() -> dict:
        """Readiness probe — returns 200 (process up, app wired)."""
        return {"status": "ok"}

    return app


__all__ = ["create_app"]