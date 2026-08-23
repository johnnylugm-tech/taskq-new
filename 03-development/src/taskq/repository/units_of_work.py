"""taskq.repository.units_of_work — per-request transaction boundary.

[FR-06] The canonical per-request ``Session`` boundary. ``unit_of_work()``
yields ONE ``Session`` per call, commits on ``__exit__``-with-no-exception,
and rolls back on ``__exit__``-with-exception (SPEC.md §3 FR-06, AC-6.2;
NFR-03 error handling + txn).

Service / route code MUST consume a Session through this context
manager — never instantiate one ad-hoc. The Session object MUST NOT
leak to the API layer except via the ``with unit_of_work() as session:``
contract (NFR-06 layering).

Citations: SPEC.md §3 FR-06, NFR-03, NFR-06; SAD.md §2.3.3 repository
layer, §4 NFR-03 enforcement site.
"""
from __future__ import annotations

import asyncio
import logging
from contextlib import contextmanager
from typing import Iterator

from sqlalchemy.orm import Session

from taskq.repository.tasks import get_session_factory

logger = logging.getLogger("taskq.repository.units_of_work")


@contextmanager
def unit_of_work() -> Iterator[Session]:
    """Open one ``Session`` per call; commit on success, rollback on exception.

    [FR-06] AC-6.2 — one Session per request, commit on clean exit,
    rollback on exception. The ``Session`` is always closed in the
    ``finally`` block so the underlying connection returns to the pool
    even when the caller's commit / rollback itself raises.

    Usage::

        with unit_of_work() as session:
            session.add(task)
            # commit happens automatically on clean exit

    If the body raises, the transaction is rolled back and the
    exception is re-raised unchanged so callers see the original
    failure.

    CancelledError (asyncio cancellation) is caught explicitly alongside
    ``Exception`` so the transaction is rolled back on cancellation
    while the cancellation itself still propagates to the caller
    (NFR-03 — CancelledError must propagate, never be swallowed).
    """
    factory = get_session_factory()
    session: Session = factory()
    try:
        yield session
        session.commit()
    except (Exception, asyncio.CancelledError):
        try:
            session.rollback()
        except Exception:
            # Rollback itself failed — preserve the original exception
            # so the caller sees the real failure, not the rollback
            # noise.
            logger.debug("rollback failed; preserving original exception",
                         exc_info=True)
        raise
    finally:
        try:
            session.close()
        except Exception:
            # Best-effort cleanup; session will be GC'd regardless.
            logger.debug("session.close failed; relying on GC", exc_info=True)


__all__ = ["unit_of_work"]
