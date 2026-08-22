"""FR-09 metrics route — admin-scoped ``GET /v1/metrics``.

[FR-09] Operator-facing observability endpoint. Returns the three named
metric payloads:

  * ``task_counts_by_status`` — task counts grouped by ``Task.status``
  * ``latency_percentiles``    — p50 / p90 / p95 / p99 of
                                 ``TaskResult.duration_ms`` (ms)
  * ``rate_limit_rejections``  — cumulative count of HTTP 429 short-
                                 circuits since process start

Auth is enforced through the single canonical
``taskq.api.deps.require_scope("admin")`` dependency (AC-4.3 single-dep
invariant). The handler is a thin shell that delegates the actual
aggregation to ``taskq.service.metrics`` so the HTTP layer stays free
of SQL and percentile math (NFR-06 layer contract).

Citations: SPEC.md §3 FR-09, §8 #10, §8 #11; SAD.md §4 api/routes/
metrics; NFR-02 (admin scope); NFR-04 (no plaintext in metrics
payload); NFR-10 (in-process integration via ASGITransport).
"""
from __future__ import annotations

from typing import Any, Dict

from fastapi import Depends

from taskq.api.deps import require_scope
from taskq.service.metrics import (
    latency_percentiles,
    rate_limit_rejections,
    task_counts_by_status,
)


def metrics(
    _auth: Dict[str, str] = Depends(require_scope("admin")),
) -> Dict[str, Any]:
    """AC-9.3 — ``GET /v1/metrics`` (admin scope) returns 3 named fields.

    The handler MUST delegate to the service-layer metric aggregators
    (NFR-06) and return a JSON object whose top-level keys are
    exactly ``task_counts_by_status``, ``latency_percentiles``, and
    ``rate_limit_rejections``. None of the fields may be ``None`` —
    an empty payload for an admin-only metrics endpoint would mask
    DB / registry outages from the operator (NFR-04 / SPEC §8 #10).
    """
    return {
        "task_counts_by_status": task_counts_by_status(),
        "latency_percentiles": latency_percentiles(),
        "rate_limit_rejections": rate_limit_rejections(),
    }


__all__ = ["metrics"]
