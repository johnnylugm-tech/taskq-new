"""FastAPI app factory — create_app().

[FR-01] Returns a FastAPI instance with the FR-01 routes mounted and the
RFC 7807 problem+json exception handlers registered. The test fixture
calls ``create_app()`` per test to get a fresh ASGI app, while the
underlying in-memory DB is reset between tests by the conftest fixture
(see 03-development/conftest.py). Citations: SPEC.md §3 FR-01; NFR-10
(integration coverage via ASGITransport); SAD.md §4 api/app.
"""
from __future__ import annotations

from fastapi import FastAPI

from taskq.api.routes.tasks import router as tasks_router
from taskq.errors.handlers import register_exception_handlers


def create_app() -> FastAPI:
    """Build a fresh FastAPI app for FR-01."""
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
    return app


__all__ = ["create_app"]
