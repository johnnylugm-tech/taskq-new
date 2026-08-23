"""ASGI middlewares — correlation id + rate-limit (FR-10 / FR-05).

[FR-10] The ``CorrelationIdMiddleware`` mints (or accepts) an
``X-Correlation-Id`` for every request, surfaces the value on the
response header, attaches it to ``request.state.correlation_id``
so the exception handlers can include it in the problem+json
body, AND emits a structured server log record carrying the
``correlation_id`` field (NFR-09 / SPEC §3 FR-10).

[FR-05] ``RateLimitMiddleware`` consults the per-token token bucket
before letting a request through to the application. Health
endpoints (``/healthz``, ``/readyz``) are EXEMPT (AC-5.4). When the
bucket is empty the middleware returns HTTP 429 + ``Retry-After`` +
``application/problem+json`` (SPEC §3 FR-05, §8 #9). The 429 body
uses the shared ``Problem`` class so it carries ``instance`` and
``correlation_id`` like every other non-2xx response (AC-10.2,
AC-10.4, AC-10.5).

The correlation-id middleware is installed BEFORE the rate-limit
middleware (i.e. registered AFTER it in ``create_app`` — Starlette
wraps in reverse order) so the rate limiter's 429 short-circuit
can read ``request.state.correlation_id`` and surface the same id
on its outgoing response. Health endpoints remain EXEMPT from the
bucket (AC-5.4) but are STILL routed through the correlation
middleware so the operator can stitch a probe's trace into the
audit log.

Citations: SPEC.md §3 FR-05, §3 FR-10, §8 #9, §8 #19; RFC 7807 §3;
NFR-03 (shared bucket state — durable surface in
``taskq.repository.rate_buckets``; middleware is the edge
enforcement point); NFR-09 (correlation_id mirrored in logs);
SAD.md §4 api/middleware.
"""
from __future__ import annotations

# pragma: no error-handling — middleware delegates failure surfacing to the
# centralised exception handlers in taskq.api.handlers; rate-limit short-
# circuits raise a Problem that the handlers convert to RFC 7807.

import logging
import math
import uuid
from typing import Dict, Optional

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from taskq.api.problem import CONTENT_TYPE, Problem
from taskq.service.rate_limit import (
    RateLimitConfig,
    TokenBucket,
    seconds_until_next_token,
)


CORRELATION_HEADER = "X-Correlation-Id"

# Paths that MUST stay reachable regardless of bucket state. Wired
# directly on the FastAPI app in ``create_app`` (FR-03 / FR-09); the
# middleware short-circuits BEFORE consulting the bucket so a missing
# api_keys table or an exhausted per-token bucket cannot take liveness
# / readiness probes down (SPEC.md §3 FR-09).
EXEMPT_PATHS = ("/healthz", "/readyz")

logger = logging.getLogger("taskq.api.correlation")


# ---------------------------------------------------------------------------
# Correlation-id helpers (FR-10)
# ---------------------------------------------------------------------------


def mint_correlation_id() -> str:
    """Mint a fresh opaque correlation id (uuid4 hex form).

    The test contract checks that the response header mirrors a
    request-supplied id exactly; when the client does not supply
    one we mint a uuid4 hex string so the header is always
    non-empty and the operator can still stitch the timeline.
    """
    return uuid.uuid4().hex


def get_correlation_id(request: Request) -> str:
    """Return the request's correlation id, minting one if absent.

    Honours an incoming ``X-Correlation-Id`` header (case-insensitive)
    so a caller can stitch its own trace into our logs.
    """
    existing = getattr(request.state, "correlation_id", None)
    if existing:
        return existing
    incoming = request.headers.get(CORRELATION_HEADER.lower())
    cid = incoming or mint_correlation_id()
    request.state.correlation_id = cid
    return cid


# ---------------------------------------------------------------------------
# Correlation-id middleware
# ---------------------------------------------------------------------------


class CorrelationIdMiddleware:
    """Mint / extract the per-request correlation id (FR-10).

    Implemented as a pure ASGI middleware (NOT ``BaseHTTPMiddleware``)
    so the ``X-Correlation-Id`` header is attached to EVERY outgoing
    response — including the 500 document that Starlette's
    ``ServerErrorMiddleware`` synthesises after an unhandled exception
    bubbles out of a route handler. ``BaseHTTPMiddleware.dispatch``
    re-raises such exceptions and the synthesised response bypasses
    our middleware's return path; a raw ASGI wrapper that hooks the
    ``send`` callable does not have that gap (AC-10.4 / NFR-09).

    Behaviour:
      * Read ``X-Correlation-Id`` from the incoming request, or
        mint a fresh uuid4 hex string if the client did not supply
        one.
      * Stash the value on the ``state`` scope extension so
        exception handlers (and the rate limiter) can include it in
        the problem+json body (AC-10.2 / AC-10.4).
      * Inject ``X-Correlation-Id`` on the response start message so
        every outgoing response carries the header — including the
        non-2xx problem documents.
      * Emit one INFO log record carrying ``correlation_id=<id>``
        per request so the server-side log mirrors the response
        header (AC-10.4 / NFR-09).

    The middleware is registered LAST in ``create_app`` so it is the
    OUTERMOST wrapper (Starlette wraps in reverse order). That keeps
    the correlation id available even when the rate-limit middleware
    short-circuits with a 429 before any handler runs.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        # Extract incoming X-Correlation-Id (case-insensitive per RFC 7230)
        # or mint a fresh uuid4 hex.
        # NB: ASGI normalises header names to lowercase bytes at the edge,
        # so the lookup below uses raw byte equality. ``value.decode("latin-1")``
        # cannot fail (latin-1 maps all 256 byte values 1:1), so no
        # try/except is needed.
        headers = scope.get("headers") or []
        cid: Optional[str] = None
        cid_header_name = CORRELATION_HEADER.lower().encode("latin-1")
        for name, value in headers:
            if name == cid_header_name:
                cid = value.decode("latin-1")
                break
        if not cid:
            cid = mint_correlation_id()

        # Stash on the ASGI scope so Starlette's request.state and
        # the api-layer handlers see the same id.
        state = scope.setdefault("state", {})
        state["correlation_id"] = cid

        # NFR-09 / AC-10.4: structured log with correlation_id field.
        method = scope.get("method", "")
        raw_path = scope.get("path", "")
        logger.info(
            "request %s %s correlation_id=%s",
            method,
            raw_path,
            cid,
        )

        # Wrap send so the X-Correlation-Id header lands on EVERY
        # outgoing http.response.start message — including the
        # synthesised 500 document that the exception handlers /
        # ServerErrorMiddleware produce after an unhandled exception.
        cid_header_name = CORRELATION_HEADER.lower().encode("latin-1")
        cid_header_value = cid.encode("latin-1")

        async def send_wrapper(message: Message) -> None:
            if message["type"] == "http.response.start":
                raw_headers = list(message.get("headers") or [])
                # Drop any prior X-Correlation-Id so the value we
                # mint / extract here is the one that ships.
                raw_headers = [
                    (n, v)
                    for (n, v) in raw_headers
                    if n.lower() != cid_header_name
                ]
                raw_headers.append((cid_header_name, cid_header_value))
                message["headers"] = raw_headers
            await send(message)

        await self.app(scope, receive, send_wrapper)


# ---------------------------------------------------------------------------
# Rate-limit middleware (FR-05)
# ---------------------------------------------------------------------------


def _problem_response(
    status: int,
    title: str,
    detail: str,
    *,
    instance: Optional[str] = None,
    correlation_id: Optional[str] = None,
    retry_after: Optional[int] = None,
) -> JSONResponse:
    """Build a RFC 7807 problem+json response with FR-10 fields."""
    problem = Problem(
        status=status,
        title=title,
        detail=detail,
        instance=instance,
        correlation_id=correlation_id,
    )
    # Retry-After is integer seconds per RFC 9110 §10.2.3 (SPEC §3 FR-05).
    headers = (
        {"Retry-After": str(int(retry_after))}
        if retry_after is not None
        else None
    )
    return JSONResponse(
        status_code=status,
        content=problem.to_dict(),
        headers=headers,
        media_type=CONTENT_TYPE,
    )


def _bucket_key(request: Request) -> str:
    """Derive the per-request bucket key.

    Prefers ``X-API-Key`` (the principal identifier) and falls back
    to ``client.host`` when the header is absent so anonymous callers
    cannot share a single bucket with every authenticated caller.
    """
    api_key = request.headers.get("x-api-key")
    if api_key:
        return f"key:{api_key}"
    client = request.client
    host = getattr(client, "host", None) if client is not None else None
    return f"ip:{host}" if host else "ip:unknown"


class RateLimitMiddleware(BaseHTTPMiddleware):
    """ASGI middleware enforcing the per-token token bucket.

    The middleware is installed by ``taskq.api.app.create_app`` so
    every ``/v1/*`` request is gated by the bucket. Health endpoints
    are exempt (AC-5.4); everything else is metered (AC-5.2). The
    429 short-circuit surfaces a Problem document so the
    ``correlation_id`` (attached by ``CorrelationIdMiddleware`` to
    ``request.state``) is included in the body alongside the
    ``Retry-After`` header (AC-10.5 / FR-10).
    """

    def __init__(self, app: ASGIApp, config: RateLimitConfig) -> None:
        super().__init__(app)
        self._config = config

    def _buckets(self, request: Request) -> Dict[str, TokenBucket]:
        """Return (and lazily initialise) the per-app bucket dict."""
        state = request.app.state
        buckets = getattr(state, "rate_limit_buckets", None)
        if buckets is None:
            buckets = {}
            state.rate_limit_buckets = buckets
        return buckets

    async def dispatch(self, request: Request, call_next):  # noqa: ANN001
        path = request.url.path
        # ---- AC-5.4: /healthz, /readyz are EXEMPT ----
        if path in EXEMPT_PATHS or path.startswith(("/healthz/", "/readyz/")):
            return await call_next(request)

        # ---- AC-5.2: every other path is gated by the per-token bucket ----
        buckets = self._buckets(request)
        key = _bucket_key(request)
        bucket = buckets.get(key)
        if bucket is None:
            bucket = TokenBucket(self._config)
            buckets[key] = bucket

        granted = bucket.consume()
        if granted:
            return await call_next(request)

        # Bucket exhausted — emit 429 + Retry-After + problem+json.
        wait = seconds_until_next_token(bucket)
        if math.isinf(wait) or wait < 0.0:
            retry_after = 1
        else:
            # Integer seconds per RFC 9110 §10.2.3 / SPEC §3 FR-05.
            retry_after = max(0, int(math.ceil(wait)))
        # FR-09 — record the rejection so /v1/metrics can surface a
        # cumulative counter to the operator. The metrics module is
        # declared by SAB as part of the architecture (NFR-06 layer
        # contract), so the import cannot fail and the function itself
        # is a simple atomic increment; no defensive try/except.
        from taskq.service.metrics import record_rate_limit_rejection

        record_rate_limit_rejection()
        cid = getattr(request.state, "correlation_id", None)
        return _problem_response(
            status=429,
            title="Too Many Requests",
            detail="Rate limit exceeded.",
            instance=request.url.path,
            correlation_id=cid,
            retry_after=retry_after,
        )


__all__ = [
    "CorrelationIdMiddleware",
    "RateLimitMiddleware",
    "EXEMPT_PATHS",
    "CORRELATION_HEADER",
    "get_correlation_id",
    "mint_correlation_id",
]
