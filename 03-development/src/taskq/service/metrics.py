"""FR-09 metric aggregation — service layer.

[FR-09] Pure-data aggregation primitives consumed by
``taskq.api.routes.metrics``. The HTTP handler delegates here so the
API layer stays free of SQL and percentile math (NFR-06 layer
contract). Three primitives are exposed:

  * ``task_counts_by_status()`` -> ``Dict[str, int]``
        Counts of ``Task`` rows grouped by ``Task.status``. An empty
        DB yields ``{}`` rather than raising.

  * ``latency_percentiles()`` -> ``Dict[str, float]``
        p50 / p90 / p95 / p99 of ``TaskResult.duration_ms`` across
        all rows that have a non-null duration. Returns zeros (not
        ``None``) when no duration rows exist so the metrics payload
        never carries a hole.

  * ``rate_limit_rejections()`` -> ``int``
        Cumulative count of HTTP 429 short-circuits recorded by the
        rate-limit middleware since process start. The middleware
        increments the counter through ``record_rate_limit_rejection``
        so this primitive always reports a current integer (never
        ``None``).

The HTTP layer never reads from the DB directly (NFR-06) — these
helpers are the single aggregation surface.

Citations: SPEC.md §3 FR-09, §8 #10, §8 #11; SAD.md §4 service/
metrics; NFR-04 (no plaintext in metrics payload); NFR-06 (no SQL
leaks past the repository).
"""
from __future__ import annotations

from typing import Dict

from taskq.repository.metrics import (
    collect_duration_ms,
    count_tasks_by_status as _repo_count_tasks_by_status,
)


# ---------------------------------------------------------------------------
# Rate-limit rejection counter (process-local; recorded by the middleware)
# ---------------------------------------------------------------------------


# Process-local monotonic counter. The middleware increments this on
# every 429 short-circuit and ``rate_limit_rejections`` reads it.
# Held in this module so the api middleware can import it without
# pulling in the SQL surface.
_REJECTION_COUNT: int = 0


def record_rate_limit_rejection() -> int:
    """Increment the rejection counter; return the post-increment value.

    Wired into ``taskq.api.middleware.RateLimitMiddleware`` so every
    429 short-circuit is observable through the FR-09 metrics
    endpoint. Idempotent across processes — the counter resets on
    restart, which matches what an operator expects from a
    since-startup metric.
    """
    global _REJECTION_COUNT
    _REJECTION_COUNT += 1
    return _REJECTION_COUNT


# ---------------------------------------------------------------------------
# Aggregation primitives
# ---------------------------------------------------------------------------


def task_counts_by_status() -> Dict[str, int]:
    """Return ``Task`` counts grouped by status.

    Empty DB returns ``{}`` rather than raising so a freshly-deployed
    binary can still report a metrics payload without a 500 (NFR-03
    fail-closed; SPEC §3 FR-09 / §8 #10).
    """
    return _repo_count_tasks_by_status()


def latency_percentiles() -> Dict[str, float]:
    """Return p50 / p90 / p95 / p99 of ``TaskResult.duration_ms`` (ms).

    Computed over every row whose ``duration_ms`` is non-null. An
    empty result set returns ``{"p50": 0.0, "p90": 0.0, "p95": 0.0,
    "p99": 0.0}`` (NEVER ``None``) so the admin metrics payload
    always carries every named field (NFR-04 / SPEC §8 #10).
    """
    samples = sorted(collect_duration_ms())
    if not samples:
        return {"p50": 0.0, "p90": 0.0, "p95": 0.0, "p99": 0.0}
    n = len(samples)

    def _pct(p: int) -> float:
        # Nearest-rank percentile over the sorted sample. Index
        # clamped to ``n - 1`` so ``p == 100`` returns the largest
        # observed value rather than raising IndexError.
        idx = min(n - 1, max(0, int(round((p / 100.0) * (n - 1)))))
        return float(samples[idx])

    return {
        "p50": _pct(50),
        "p90": _pct(90),
        "p95": _pct(95),
        "p99": _pct(99),
    }


def rate_limit_rejections() -> int:
    """Return the cumulative count of rate-limit rejections since start.

    The middleware increments the counter through
    ``record_rate_limit_rejection`` on every 429 short-circuit; this
    primitive is the read-only observer for ``GET /v1/metrics``.
    Always returns a non-negative integer (never ``None``) so the
    admin metrics payload never carries a hole (NFR-04).
    """
    return int(_REJECTION_COUNT)


__all__ = [
    "task_counts_by_status",
    "latency_percentiles",
    "rate_limit_rejections",
    "record_rate_limit_rejection",
]
