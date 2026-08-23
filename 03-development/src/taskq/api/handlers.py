"""FastAPI exception handlers — Problem → application/problem+json.

[FR-01] Maps Problem + pydantic validation + unhandled exceptions to
application/problem+json. Lives in the api layer to honour the NFR-06
independence constraint between ``taskq.api`` and ``taskq.errors``.

[FR-10] Every Problem response carries the FR-10 contract fields
(``type``, ``title``, ``status``, ``detail``, ``instance``,
``correlation_id``). The handlers enrich the outgoing Problem with
``instance`` (the request path) and ``correlation_id`` (the
per-request id attached by ``CorrelationIdMiddleware``) BEFORE
serializing, so a Problem raised deep in the route layer does not
need to know the request path or correlation id to satisfy the
FR-10 contract. The ``X-Correlation-Id`` response header is set
on the outgoing ``JSONResponse`` so the AC-10.4 mirror invariant
holds even when Starlette's ``ServerErrorMiddleware`` synthesises
the final response after an unhandled exception bubbles past
``ExceptionMiddleware`` (AC-10.4 / NFR-09).

Citations: SPEC.md §3 FR-01, §3 FR-10, §7 (status map), §8 #19;
NFR-02 (no stack/SQL/path leak); NFR-03 (CancelledError naturally
propagates in asyncio — we do not register a handler that could
swallow it); SAD.md §4 api/handlers.
"""
from __future__ import annotations

# pragma: no error-handling — this module IS the centralised error handler
# (FastAPI @app.exception_handler decorator). All Problem / RequestValidation
# / unhandled-exception routing happens here, by design — NFR-03.

import logging
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from taskq.api.middleware import CORRELATION_HEADER
from taskq.api.problem import CONTENT_TYPE, Problem

logger = logging.getLogger("taskq.api.handlers")


def _enrich_problem(request: Request, exc: Problem) -> Problem:
    """Backfill ``instance`` and ``correlation_id`` from the request.

    A Problem raised deep in the route layer never sets these fields
    itself (it has no access to the request); the handlers do it
    centrally so the FR-10 contract is satisfied uniformly.

    For HTTP 403 (insufficient scope) we deliberately omit
    ``instance`` — the request path embeds the resource identifier
    and surfacing it in the body would leak resource existence
    (NFR-02 / SPEC §8 #6 / FR-04 AC-4.2).
    """
    if not exc.instance and exc.status != 403:
        exc.instance = request.url.path
    if not exc.correlation_id:
        cid = getattr(request.state, "correlation_id", None)
        if cid:
            exc.correlation_id = cid
    return exc


def _problem_response(request: Request, problem: Problem) -> JSONResponse:
    enriched = _enrich_problem(request, problem)
    # AC-10.4 — mirror the correlation id on the response header so
    # the operator can stitch client + server timelines even when
    # the final response is synthesised by Starlette's
    # ``ServerErrorMiddleware`` after an unhandled exception
    # bubbles past ``ExceptionMiddleware`` (NFR-09).
    headers = (
        {CORRELATION_HEADER: enriched.correlation_id}
        if enriched.correlation_id
        else None
    )
    return JSONResponse(
        status_code=enriched.status,
        content=enriched.to_dict(),
        media_type=CONTENT_TYPE,
        headers=headers,
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
    async def _problem_handler(request: Request, exc: Problem) -> JSONResponse:  # noqa: ANN001
        return _problem_response(request, exc)

    @app.exception_handler(RequestValidationError)
    async def _validation_handler(
        request: Request, exc: RequestValidationError  # noqa: ANN001
    ) -> JSONResponse:
        return _problem_response(
            request,
            _validation_problem("Request validation failed", exc.errors()),
        )

    @app.exception_handler(Exception)
    async def _unhandled_handler(request: Request, exc: Exception) -> JSONResponse:  # noqa: ANN001
        # NFR-02: do not leak stack/SQL/path/schema. Log full trace server-side,
        # return a generic 500 problem document to the client.
        logger.exception("unhandled exception: %s", exc)
        return _problem_response(
            request,
            Problem(status=500, title="Internal server error", detail="An unexpected error occurred."),
        )


__all__ = ["register_exception_handlers", "CONTENT_TYPE"]
