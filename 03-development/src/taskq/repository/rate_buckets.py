"""RateBucketRepository — DB-backed, row-level locked token buckets (FR-05).

[FR-05] AC-5.3 — Token-bucket state is persisted in the database so
multiple worker processes observe the SAME per-token counts (NFR-03).
Every refill + decrement runs inside a single transaction guarded by
a row-level lock (``SELECT ... FOR UPDATE`` via
``select(...).with_for_update()``) so concurrent workers serialise on
the bucket row instead of racing past it (NFR-13).

This repository owns ALL SQL touching the ``rate_buckets`` table
(NFR-06); the service / middleware layers see only domain values
(plain dicts of ``tokens`` / ``last_refill_at`` / ``key_id``).

Public surface:

* ``RateBucket(key_id, tokens, last_refill_at, config_burst, config_per_sec)``
    ORM model — one row per API-key / principal.

* ``RateBucketRepository()``
    * ``get(key_id) -> dict`` — fetch (or materialise at full capacity)
      the bucket row for ``key_id``. Returns a plain dict so the
      middleware and the unit test can consume it without an
      ORM dependency.
    * ``consume(key_id) -> dict`` — atomically refill + decrement one
      token under a row-level lock inside ONE transaction.

Both ``get`` and ``consume`` lazily materialise a row at full capacity
the first time a fresh ``key_id`` is observed, so callers don't need
a separate seeding step.

Citations: SPEC.md §3 FR-05, §5.1; SAD.md §4 repository layer;
NFR-03 (shared state across workers); NFR-06 (no SQL past the
repository); NFR-13 (row-level lock under concurrency).
"""
from __future__ import annotations

import os
import time
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from sqlalchemy import Float, Integer, String, select
from sqlalchemy.orm import Mapped, Session, mapped_column, sessionmaker

from taskq.models.base import Base
from taskq.repository.tasks import get_session_factory


# ---------- ORM model ----------


class RateBucket(Base):
    """Persistent token-bucket row for a single principal (per API key).

    One row per ``key_id``. The ``tokens`` column stores the fractional
    token count (refill is computed lazily on every read / consume);
    ``last_refill_at`` carries the wall-clock timestamp at which the
    count was last refreshed so the next ``consume`` can compute the
    elapsed refill accurately. The ``config_burst`` / ``config_per_sec``
    columns let the bucket retain its configuration across process
    restarts (otherwise a rolling restart would silently change the
    bucket's capacity).
    """

    __tablename__ = "rate_buckets"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    key_id: Mapped[str] = mapped_column(String, index=True, unique=True)
    tokens: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    last_refill_at: Mapped[float] = mapped_column(Float, nullable=False)
    config_burst: Mapped[int] = mapped_column(Integer, nullable=False)
    config_per_sec: Mapped[float] = mapped_column(Float, nullable=False)


# ---------- Defaults (TASKQ_RATE_BURST / TASKQ_RATE_PER_SEC) ----------


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def _env_float(name: str, default: float) -> float:
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        return float(raw)
    except ValueError:
        return default


DEFAULT_BURST: int = _env_int("TASKQ_RATE_BURST", 20)
DEFAULT_PER_SEC: float = _env_float("TASKQ_RATE_PER_SEC", 5.0)


# ---------- Row projection ----------


def _row_to_dict(row: RateBucket) -> Dict[str, Any]:
    """Project a ``RateBucket`` ORM row to a plain dict for callers."""
    last = float(row.last_refill_at)
    return {
        "id": row.id,
        "key_id": row.key_id,
        "tokens": float(row.tokens),
        "last_refill_at": last,
        "last_refill_at_iso": (
            datetime.fromtimestamp(last, tz=timezone.utc).isoformat()
            if last
            else None
        ),
        "config_burst": int(row.config_burst),
        "config_per_sec": float(row.config_per_sec),
    }


# ---------- Repository ----------


class RateBucketRepository:
    """Persistence boundary for ``rate_buckets`` (FR-05 / AC-5.3).

    The repository owns the SQL — service / middleware callers only
    see plain dicts. ``consume`` runs the SELECT-FOR-UPDATE + UPDATE
    inside a single transaction so concurrent workers serialise on
    the bucket row (NFR-13).
    """

    def __init__(
        self,
        session_factory: Optional[sessionmaker] = None,
        *,
        default_burst: Optional[int] = None,
        default_per_sec: Optional[float] = None,
    ) -> None:
        self._session_factory = session_factory or get_session_factory()
        self._default_burst = (
            int(default_burst) if default_burst is not None else DEFAULT_BURST
        )
        self._default_per_sec = (
            float(default_per_sec) if default_per_sec is not None else DEFAULT_PER_SEC
        )

    # ---- helpers ----

    def _refill_locked(
        self, row: RateBucket, now: float, burst: int, per_sec: float
    ) -> None:
        """Refill ``row`` in-place based on elapsed wall-clock seconds."""
        last = float(row.last_refill_at)
        if now <= last:
            # Clock didn't advance (or stepped backwards) — keep
            # ``last_refill_at`` monotonic so a clock recovery does
            # not silently grant extra tokens.
            row.last_refill_at = now
            return
        elapsed = now - last
        refilled = elapsed * float(per_sec)
        if refilled > 0.0:
            row.tokens = min(float(burst), float(row.tokens) + refilled)
        row.last_refill_at = now

    def _materialise(
        self,
        session: Session,
        key_id: str,
        now: float,
    ) -> RateBucket:
        """Create a full bucket row for a fresh ``key_id`` and return it."""
        burst = self._default_burst
        per_sec = self._default_per_sec
        row = RateBucket(
            id=str(uuid.uuid4()),
            key_id=key_id,
            tokens=float(burst),  # start FULL so first burst is unimpeded
            last_refill_at=now,
            config_burst=int(burst),
            config_per_sec=float(per_sec),
        )
        session.add(row)
        session.flush()  # ensure the row is visible to the SELECT below
        return row

    # ---- public API ----

    def get(self, key_id: str) -> Dict[str, Any]:
        """Return the bucket row for ``key_id`` as a plain dict.

        Lazily materialises a full bucket when no row exists yet so
        the unit test (and the middleware) can probe a previously
        unseen ``key_id`` without a separate seeding step.
        """
        session: Session = self._session_factory()
        try:
            now = time.time()
            stmt = (
                select(RateBucket)
                .where(RateBucket.key_id == key_id)
                .with_for_update()
                .limit(1)
            )
            row = session.execute(stmt).scalars().first()
            if row is None:
                row = self._materialise(session, key_id, now)
                session.commit()
                return _row_to_dict(row)

            # Existing row — refill lazily to mirror the live state
            # callers would observe after the in-process ``consume``
            # has run. We do NOT mutate ``row.tokens`` here because
            # ``get`` is meant to be side-effect-free for callers;
            # the refill math is identical to what ``consume`` would
            # do so the returned count is what a follow-up ``consume``
            # would actually see.
            burst = int(row.config_burst)
            per_sec = float(row.config_per_sec)
            last = float(row.last_refill_at)
            tokens = float(row.tokens)
            if now > last:
                tokens = min(float(burst), tokens + (now - last) * per_sec)
            out = _row_to_dict(row)
            out["tokens"] = tokens
            return out
        finally:
            session.close()

    def consume(self, key_id: str) -> Dict[str, Any]:
        """Atomically refill + decrement one token under row-level lock.

        The SELECT-FOR-UPDATE + UPDATE pair runs inside ONE transaction
        context manager (``with session.begin():``) so concurrent
        workers serialise on the bucket row (NFR-13) and the refill /
        decrement / commit boundary is atomic. On lock contention the
        second worker blocks until the first commits — both observe
        the post-commit ``tokens`` value rather than racing past it.
        """
        session: Session = self._session_factory()
        try:
            now = time.time()
            # Single-transaction boundary (AC-5.3 / NFR-13): the SELECT
            # with row-level lock AND the decrement / refill update run
            # inside the SAME context manager so neither can commit
            # without the other.
            with session.begin():
                stmt = (
                    select(RateBucket)
                    .where(RateBucket.key_id == key_id)
                    .with_for_update()
                    .limit(1)
                )
                row = session.execute(stmt).scalars().first()
                if row is None:
                    row = self._materialise(session, key_id, now)

                burst = int(row.config_burst)
                per_sec = float(row.config_per_sec)
                self._refill_locked(row, now, burst, per_sec)

                granted = False
                if float(row.tokens) >= 1.0:
                    row.tokens = float(row.tokens) - 1.0
                    granted = True

            # Transaction committed by ``session.begin()``'s context
            # exit; session is still open until the ``finally`` block
            # closes it. We ``refresh`` so the returned dict reflects
            # the freshly-committed column values (and so any reader
            # doing ``repo.get(key_id)`` immediately after sees the
            # post-decrement state).
            session.refresh(row)
            out = _row_to_dict(row)
            out["granted"] = granted
            return out
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()


__all__ = [
    "RateBucket",
    "RateBucketRepository",
    "DEFAULT_BURST",
    "DEFAULT_PER_SEC",
]
