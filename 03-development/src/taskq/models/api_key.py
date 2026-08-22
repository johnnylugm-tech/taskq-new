"""APIKey ORM model.

[FR-03] Persistent record for an issued API key. The plaintext is NEVER
stored — only ``key_hash`` (a 64-char lowercase hex SHA-256) lives in
the row (SPEC.md §3 FR-03, §8 #18). The repository reads / writes via
``taskq.repository.keys.APIKeyRepository`` (NFR-06).

Columns:
    id          — UUID string PK
    key_hash    — 64-char lowercase hex SHA-256 of the plaintext key
    scope       — capability string ("read" / "write" / "admin")
    created_at  — UTC timestamp set at row insert time
    revoked_at  — UTC timestamp when the key was disabled (NULL == active)

Citations: SPEC.md §3 FR-03, §7, §8 #5, #18; SAD.md §4 Models layer;
NFR-02 (no plaintext on the wire / in logs / metrics); NFR-04 (no
plaintext in logs / error body / metrics).
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import DateTime, String
from sqlalchemy.orm import Mapped, mapped_column

from taskq.models.base import Base


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _new_uuid() -> str:
    return str(uuid.uuid4())


class APIKey(Base):
    """A persistent API key row (FR-03).

    Carries only the SHA-256 hash of the plaintext key — never the
    plaintext itself (SPEC §8 #18). ``revoked_at`` flags a key as
    invalid without deleting it, preserving audit history.
    """

    __tablename__ = "api_keys"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_new_uuid)
    key_hash: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    scope: Mapped[str] = mapped_column(String(32), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utc_now
    )
    revoked_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


__all__ = ["APIKey"]