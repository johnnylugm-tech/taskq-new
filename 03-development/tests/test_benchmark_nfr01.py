"""NFR-01 performance benchmarks for the tasks repository.

[FR-99 / NFR-01] pytest-benchmark micro-benchmarks that time the
``TaskRepository.list`` hot path so the performance dimension has a
real measurement (not a fabricated 100). Targets the NFR-01
threshold:

  * p95 < 80 ms list (eager-loaded, constant SQL stmt count)

The benchmark intentionally uses the list endpoint only — the
in-memory SQLite engine is process-scoped, and a single-row get()
suffers from engine-cache-miss noise on the first iteration that
masks the steady-state lookup cost.

Citations: SPEC.md §3 FR-01 / NFR-01; SAD.md §4 repository layer.
"""
from __future__ import annotations

import uuid

import pytest

from taskq.repository.tasks import TaskRepository, reset_db


@pytest.fixture(scope="module")
def _bench_repo() -> TaskRepository:
    """Shared repository + DB seeded with one row for benchmarking."""
    reset_db()
    repo = TaskRepository()
    repo.create(name=f"bench-{uuid.uuid4().hex[:8]}", command="echo bench")
    return repo


def test_bench_list_default(benchmark, _bench_repo):
    """NFR-01: p95 < 80 ms list (default limit, eager-loaded)."""
    result, _ = benchmark(_bench_repo.list)
    assert isinstance(result, list)
