"""Per-token token-bucket rate limiting — service layer (FR-05).

[FR-05] Implements the in-process token-bucket primitive that the HTTP
middleware uses to gate /v1/* requests (AC-5.1, AC-5.2, AC-5.4). The
DB-backed, row-level-locked variant lives in
``taskq.repository.rate_buckets`` (AC-5.3); the middleware consults the
shared bucket state via the ``RateBucketRepository`` so multiple worker
processes observe the same per-token counts (NFR-03).

Public surface:

* ``RateLimitConfig(burst, per_sec)`` — plain value container
  carrying the configured ``TASKQ_RATE_BURST`` (capacity) and
  ``TASKQ_RATE_PER_SEC`` (refill rate) values (SPEC.md §5.1).

* ``TokenBucket(config)`` — in-memory token bucket for a single
  principal. Holds at most ``config.burst`` tokens at any instant,
  refills at ``config.per_sec`` per real second of elapsed time, and
  reports the wall-clock time at which the next token becomes
  available (used to fill the ``Retry-After`` header on 429 — SPEC.md
  §3 FR-05, §8 #9).

* ``consume_token(bucket, now=None) -> bool`` — refill the bucket
  using ``now`` (or ``time.time()`` when omitted), then attempt to
  consume a single token. Returns ``True`` on success and ``False``
  when the bucket is empty.

* ``seconds_until_next_token(bucket, now=None) -> float`` — report
  the wall-clock seconds until at least one token reaches the
  bucket. Used by the API middleware to compute the ``Retry-After``
  header (HTTP semantics: integer seconds, per RFC 9110 §10.2.3).

The service layer owns NO SQL — the persistent bucket row lives in
``taskq.repository.rate_buckets`` (NFR-06). This module is the
unit-level mirror used by AC-5.1 and is re-used by the HTTP
middleware as the in-memory primitive when no DB row is needed.

Citations: SPEC.md §3 FR-05, §5.1, §8 #9; SAD.md §4 service layer;
NFR-03 (shared state across workers); NFR-06 (no SQL leaks past the
repository).
"""
from __future__ import annotations

import math
import time
from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class RateLimitConfig:
    """Per-token bucket configuration (SPEC.md §5.1).

    Fields mirror the environment variables TASKQ_RATE_BURST (capacity)
    and TASKQ_RATE_PER_SEC (refill rate). ``frozen=True`` so the same
    config object can be safely shared between the HTTP middleware,
    the repository, and the test suite without one caller mutating it
    out from under another.
    """

    burst: int
    per_sec: float


class TokenBucket:
    """In-memory per-token bucket — holds at most ``config.burst`` tokens.

    The bucket is purely a runtime value object: it does not touch
    the database and is safe to construct inside a request handler.
    For cross-worker persistence see
    ``taskq.repository.rate_buckets.RateBucketRepository`` (AC-5.3).

    Invariants:
      * ``tokens`` is in ``[0.0, config.burst]``
      * Refill is computed lazily on every ``consume_token`` call
        using ``now - last_refill_at`` so the bucket never under- or
        over-counts tokens even when the host clock steps backwards.
    """

    __slots__ = ("_config", "_tokens", "_last_refill_at")

    def __init__(
        self,
        config: RateLimitConfig,
        *,
        initial_tokens: Optional[float] = None,
        now: Optional[float] = None,
    ) -> None:
        self._config = config
        # A fresh bucket starts FULL so a brand-new principal can
        # immediately spend ``burst`` tokens before being throttled.
        full = float(config.burst)
        self._tokens = full if initial_tokens is None else float(initial_tokens)
        if self._tokens < 0.0:
            self._tokens = 0.0
        if self._tokens > full:
            self._tokens = full
        self._last_refill_at = float(now) if now is not None else time.time()

    # ---- Properties (read-only) ----

    @property
    def config(self) -> RateLimitConfig:
        """The bucket's configuration (burst, per_sec)."""
        return self._config

    @property
    def tokens(self) -> float:
        """Current token count (lazy-refill NOT applied — see ``consume_token``)."""
        return self._tokens

    # ---- Mutators ----

    def _refill_locked(self, now: float) -> None:
        """Bring the bucket up to date with elapsed wall-clock time."""
        if now <= self._last_refill_at:
            # Clock didn't advance (or stepped backwards): nothing to
            # refill, but keep ``_last_refill_at`` monotonic to avoid
            # accidentally granting tokens when the clock recovers.
            self._last_refill_at = now
            return
        elapsed = now - self._last_refill_at
        refilled = elapsed * self._config.per_sec
        if refilled <= 0.0:
            return
        self._tokens = min(float(self._config.burst), self._tokens + refilled)
        self._last_refill_at = now

    def consume(self, now: Optional[float] = None) -> bool:
        """Try to consume a single token; return True on success."""
        ts = time.time() if now is None else float(now)
        self._refill_locked(ts)
        if self._tokens >= 1.0:
            self._tokens -= 1.0
            return True
        return False

    def seconds_until_next_token(self, now: Optional[float] = None) -> float:
        """Seconds until the bucket holds at least one token.

        Returns ``0.0`` when the bucket already has a token ready.
        Used by the API middleware to fill the ``Retry-After`` header
        on a 429 response (SPEC.md §3 FR-05, §8 #9, RFC 9110 §10.2.3
        — integer seconds).
        """
        ts = time.time() if now is None else float(now)
        self._refill_locked(ts)
        if self._tokens >= 1.0:
            return 0.0
        deficit = 1.0 - self._tokens
        rate = float(self._config.per_sec)
        if rate <= 0.0:
            # No refill possible — surface a large wait so callers
            # back off rather than tight-looping.
            return math.inf
        return deficit / rate


# ---- Functional entry points (preferred public surface) ----


def consume_token(
    bucket: TokenBucket, now: Optional[float] = None
) -> bool:
    """Refill ``bucket`` to ``now`` and try to consume a single token.

    This is the free-function entry point used by both the HTTP
    middleware and the AC-5.1 unit test. It is intentionally a thin
    wrapper around ``TokenBucket.consume`` so callers don't need to
    reach for the method form when working with a local bucket
    reference.
    """
    return bucket.consume(now=now)


def seconds_until_next_token(
    bucket: TokenBucket, now: Optional[float] = None
) -> float:
    """Wrap ``TokenBucket.seconds_until_next_token`` as a free function."""
    return bucket.seconds_until_next_token(now=now)


__all__ = [
    "RateLimitConfig",
    "TokenBucket",
    "consume_token",
    "seconds_until_next_token",
]
