"""Rate-limit ASGI middleware — per-token bucket enforcement (FR-05).

[FR-05] AC-5.2 / AC-5.4 — A ``BaseHTTPMiddleware`` that consults the
per-token token bucket before letting a request through to the
application. Health endpoints (``/healthz``, ``/readyz``) are
EXEMPT (AC-5.4). When the bucket is empty the middleware returns:

  * HTTP status ``429``
  * ``Retry-After`` header — integer seconds until the next token is
    available (SPEC.md §3 FR-05, §8 #9)
  * ``application/problem+json`` body — RFC 7807 problem document

The bucket itself is the in-memory ``TokenBucket`` from
``taskq.service.rate_limit``; the DB-backed, row-level-locked
variant lives in ``taskq.repository.rate_buckets`` and is used
directly by AC-5.3. The middleware holds a ``dict[key, TokenBucket]``
on ``app.state`` so each fresh ``create_app()`` (one per test,
per FR-01) starts with empty bucket state.

The bucket key is the request's ``X-API-Key`` header value when
present, otherwise the client's source address. Per-key isolation
is the contract (SPEC.md §3 FR-05): one principal cannot deplete
another's tokens.

The middleware is intentionally narrow: it does NOT implement the
shared DB-backed state — that lives in the repository (NFR-13,
AC-5.3). The middleware is the HTTP-edge enforcement point that
makes the in-process bucket visible at the API boundary so the
``/v1/*`` request volume is bounded.

Citations: SPEC.md §3 FR-05, §8 #9; SAD.md §4 api layer;
NFR-03 (shared state across workers — the underlying repository
is the durable surface; the middleware is the edge enforcement
point); NFR-10 (integration boundary).
"""
from __future__ import annotations

import math
from typing import Any, Dict, Optional

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.types import ASGIApp

from taskq.service.rate_limit import (
    RateLimitConfig,
    TokenBucket,
    seconds_until_next_token,
)


# Paths that MUST stay reachable regardless of bucket state. Wired
# directly on the FastAPI app in ``create_app`` (FR-03 / FR-09); the
# middleware short-circuits BEFORE consulting the bucket so a missing
# api_keys table or an exhausted per-token bucket cannot take liveness
# / readiness probes down (SPEC.md §3 FR-09).
EXEMPT_PATHS = ("/healthz", "/readyz")

PROBLEM_CONTENT_TYPE = "application/problem+json"


def _problem_response(
    status: int,
    title: str,
    detail: str,
    *,
    retry_after: Optional[int] = None,
) -> JSONResponse:
    """Build a RFC 7807 problem+json response with an optional ``Retry-After``."""
    body: Dict[str, Any] = {
        "type": "about:blank",
        "title": title,
        "status": status,
        "detail": detail,
    }
    headers: Optional[Dict[str, str]] = None
    if retry_after is not None:
        headers = {"Retry-After": str(int(retry_after))}
    return JSONResponse(
        status_code=status,
        content=body,
        headers=headers,
        media_type=PROBLEM_CONTENT_TYPE,
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
    are exempt (AC-5.4); everything else is metered (AC-5.2).
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
        return _problem_response(
            status=429,
            title="Too Many Requests",
            detail="Rate limit exceeded.",
            retry_after=retry_after,
        )


__all__ = ["RateLimitMiddleware", "EXEMPT_PATHS"]
