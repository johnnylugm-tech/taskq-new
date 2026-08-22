"""FastAPI app factory — create_app().

[FR-01] Returns a FastAPI instance with the FR-01 routes mounted and the
RFC 7807 problem+json exception handlers registered.

[FR-02] Additionally mounts the FR-02 runs router under the same
``/v1/tasks`` prefix so POST ``/{task_id}/run`` and GET ``/{task_id}/runs``
resolve.

[FR-03] Adds unauthenticated ``/healthz`` and ``/readyz`` endpoints at
the root prefix (SPEC.md §3 FR-03 AC-3.6, FR-09). These are wired
directly on the app (no router, no auth dependency) so that health
checks succeed even when the api_keys table is unreachable.

The test fixture calls ``create_app()`` per test to get a fresh ASGI
app, while the underlying in-memory DB is reset between tests by the
conftest fixture (see 03-development/conftest.py).

Citations: SPEC.md §3 FR-01, §3 FR-02, §3 FR-03 (AC-3.6) / FR-09;
NFR-10 (integration coverage via ASGITransport); SAD.md §4 api/app.
"""
from __future__ import annotations

from fastapi import FastAPI

from taskq.api.handlers import register_exception_handlers
from taskq.api.routes.runs import router as runs_router
from taskq.api.routes.tasks import router as tasks_router


def create_app() -> FastAPI:
    """Build a fresh FastAPI app for FR-01 + FR-02 + FR-03."""
    app = FastAPI(
        title="taskq API",
        version="0.1.0",
        # Disable the default redirect_slashes behaviour so that 422
        # validation responses aren't turned into 307 redirects (which
        # would break the problem+json contract from SPEC §10 / FR-10).
        redirect_slashes=False,
    )
    register_exception_handlers(app)
    app.include_router(tasks_router, prefix="/v1/tasks", tags=["tasks"])
    app.include_router(runs_router, prefix="/v1/tasks", tags=["runs"])

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