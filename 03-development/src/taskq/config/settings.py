"""Settings — runtime configuration for taskq.

[FR-06] Exposes ``Settings`` with ``db_pool_size`` /
``db_pool_pre_ping`` fields read from ``TASKQ_DB_POOL_SIZE`` /
``TASKQ_DB_POOL_PRE_PING`` environment variables. Defaults match
SPEC §3 FR-06: ``pool_size=5``, ``pool_pre_ping=True``.

The engine factory (``taskq.repository.tasks._build_engine``) consults
the same settings object so the configured pool size and pre-ping
flag are observable on the resulting engine (AC-6.5).

Citations: SPEC.md §3 FR-06 (pool_size + pool_pre_ping), §5.1
(env-driven configuration).
"""
from __future__ import annotations

import os
from dataclasses import dataclass


def _env_bool(name: str, default: bool) -> bool:
    """Return the bool value of env var ``name``; ``default`` when unset."""
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


def _env_int(name: str, default: int) -> int:
    """Return the int value of env var ``name``; ``default`` when unset / invalid."""
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


@dataclass(frozen=True)
class Settings:
    """Frozen runtime configuration shared across the process.

    Fields:
        db_pool_size      — connection-pool size (env TASKQ_DB_POOL_SIZE,
                            default 5 per SPEC §3 FR-06).
        db_pool_pre_ping  — enable ``pool_pre_ping`` (env
                            TASKQ_DB_POOL_PRE_PING, default True).

    The dataclass is ``frozen=True`` so the same instance can be safely
    passed through the engine factory, the repository, and the test
    harness without one caller mutating it under another.
    """

    db_pool_size: int = 5
    db_pool_pre_ping: bool = True

    @classmethod
    def from_env(cls) -> "Settings":
        """Construct a Settings from process env (falls back to defaults)."""
        return cls(
            db_pool_size=_env_int("TASKQ_DB_POOL_SIZE", 5),
            db_pool_pre_ping=_env_bool("TASKQ_DB_POOL_PRE_PING", True),
        )


__all__ = ["Settings"]
