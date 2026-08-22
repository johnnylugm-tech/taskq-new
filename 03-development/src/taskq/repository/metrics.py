"""FR-09 metrics repository — persistence boundary for the metrics aggregator.

[FR-09] All SQL touching ``tasks`` / ``task_results`` for the
``GET /v1/metrics`` aggregator lives here (NFR-06 — service layer MUST
NOT import SQLAlchemy primitives). The repository returns plain
Python types only so ``taskq.service.metrics`` can aggregate them
without an ORM dependency.

Public surface:

* ``count_tasks_by_status() -> Dict[str, int]`` — counts of ``Task``
  rows grouped by ``Task.status``. Empty DB yields ``{}``.
* ``collect_duration_ms() -> List[int]`` — non-null ``duration_ms``
  values from ``task_results`` (used to compute p50/p90/p95/p99 at
  the service layer).

The repository owns no business semantics — it returns raw
observations and lets the service layer turn them into the named
percentile fields surfaced at /v1/metrics.

Citations: SPEC.md §3 FR-09, §8 #10, §8 #11; SAD.md §4 repository/
metrics; NFR-06 (no SQL past the repository).
"""
from __future__ import annotations

from typing import Dict, List

from sqlalchemy import func, select

from taskq.models.task import Task
from taskq.models.task_result import TaskResult
from taskq.repository.tasks import get_session_factory


def count_tasks_by_status() -> Dict[str, int]:
    """Return ``Task`` counts grouped by status.

    Empty DB returns ``{}`` rather than raising so a freshly-deployed
    binary can still report a metrics payload without a 500 (NFR-03
    fail-closed; SPEC §3 FR-09 / §8 #10).
    """
    factory = get_session_factory()
    with factory() as session:
        rows = session.execute(
            select(Task.status, func.count(Task.id)).group_by(Task.status)
        ).all()
    return {str(status): int(count) for status, count in rows}


def collect_duration_ms() -> List[int]:
    """Return every non-null ``TaskResult.duration_ms`` value.

    Used by the service layer to compute the FR-09 percentile fields.
    Empty result set returns ``[]``; the caller is responsible for
    substituting zeros when no samples are present.
    """
    factory = get_session_factory()
    with factory() as session:
        durations = (
            session.execute(select(TaskResult.duration_ms))
            .scalars()
            .all()
        )
    return [int(d) for d in durations if d is not None]


__all__ = ["count_tasks_by_status", "collect_duration_ms"]
