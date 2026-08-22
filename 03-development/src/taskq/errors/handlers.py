"""FastAPI exception handlers — convert exceptions to problem+json.

[FR-01] Maps Problem + pydantic validation + everything else to
application/problem+json. Citations: SPEC.md §3 FR-01 (FR-10 contract);
NFR-02 (no stack/SQL/path leak); NFR-03 (CancelledError naturally
propagates in asyncio — we do not register a handler that could swallow
it); SAD.md §4 errors layer.
"""
from __future__ import annotations

import logging
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from taskq.errors.problem import Problem

logger = logging.getLogger("taskq.errors")

CONTENT_TYPE = "application/problem+json"


def _problem_response(problem: Problem) -> JSONResponse:
    return JSONResponse(
        status_code=problem.status,
        content=problem.to_dict(),
        media_type=CONTENT_TYPE,
    )


def _validation_problem(detail: str, errors: Any) -> Problem:
    return Problem(
        status=422,
        title="Validation failed",
        detail=detail,
        type="about:blank",
        extra={"errors": errors} if errors else None,
    )


def register_exception_handlers(app: FastAPI) -> None:
    """Attach problem+json handlers to the FastAPI app."""

    @app.exception_handler(Problem)
    async def _problem_handler(_request: Request, exc: Problem) -> JSONResponse:  # noqa: ANN001
        return _problem_response(exc)

    @app.exception_handler(RequestValidationError)
    async def _validation_handler(
        _request: Request, exc: RequestValidationError  # noqa: ANN001
    ) -> JSONResponse:
        return _problem_response(_validation_problem("Request validation failed", exc.errors()))

    @app.exception_handler(Exception)
    async def _unhandled_handler(_request: Request, exc: Exception) -> JSONResponse:  # noqa: ANN001
        # NFR-02: do not leak stack/SQL/path/schema. Log full trace server-side,
        # return a generic 500 problem document to the client.
        logger.exception("unhandled exception: %s", exc)
        return _problem_response(
            Problem(status=500, title="Internal server error", detail="An unexpected error occurred.")
        )
