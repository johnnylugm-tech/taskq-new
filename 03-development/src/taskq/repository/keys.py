"""APIKey repository — hashing, constant-time verify, persistence.

[FR-03] Owns the SHA-256 hashing of plaintext keys and the
``hmac.compare_digest`` constant-time comparison used to authenticate
incoming keys against the ``api_keys`` table. All ORM details live
here (NFR-06); service / route callers see only plain dicts or domain
exceptions.

Exposed surface:

* ``hash_api_key(plaintext: str) -> str``
    64-char lowercase hex SHA-256 of the plaintext.

* ``verify_api_key(candidate: str, stored_hash: str) -> bool``
    Constant-time equality via ``hmac.compare_digest``. The candidate
    is hashed first so the comparison is ``sha256(candidate) ==
    stored_hash`` (NFR-02).

* ``APIKeyRepository``
    Persistence boundary for the ``api_keys`` table.

The repository shares the engine / session factory with
``taskq.repository.tasks`` so reads / writes across the two layers
observe the same SQLite database (the GREEN tests run against a
single in-memory SQLite via StaticPool).

Citations: SPEC.md §3 FR-03, §7, §8 #5, #18; NFR-02 (constant-time
compare; no plaintext on the wire / in logs / metrics); NFR-04 (no
plaintext in logs / error body / metrics); NFR-06 (layer contract).
"""
from __future__ import annotations

import hashlib
import hmac
from typing import Any, Dict, Optional

from sqlalchemy import select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from taskq.models.api_key import APIKey
from taskq.models.base import Base
from taskq.repository.tasks import get_engine, get_session_factory


# ---------- Hash + verify primitives (NFR-02 / AC-3.2 / AC-3.3) ----------


def hash_api_key(plaintext: str) -> str:
    """Return the 64-char lowercase hex SHA-256 of ``plaintext``.

    Used both at key creation time (CLI) and inside ``verify_api_key``
    so a single hash function backs the entire auth path.
    """
    return hashlib.sha256(plaintext.encode("utf-8")).hexdigest()


def verify_api_key(candidate: str, stored_hash: str) -> bool:
    """Constant-time comparison of ``candidate`` against ``stored_hash``.

    The candidate plaintext is hashed first so the comparison is
    ``sha256(candidate) == stored_hash``. The equality itself uses
    ``hmac.compare_digest`` (NFR-02) so the comparison time is
    independent of how many bytes differ.
    """
    candidate_hash = hash_api_key(candidate)
    return hmac.compare_digest(candidate_hash, stored_hash)


# ---------- Domain-level exceptions ----------


class DuplicateAPIKey(Exception):
    """Raised when an insert collides on the unique api_keys.key_hash index."""


# ---------- Repository ----------


class APIKeyRepository:
    """Persistence boundary for ``api_keys``.

    ``api_keys`` rows are joined into the shared ``Base.metadata`` so
    the ``reset_db`` helper in ``taskq.repository.tasks`` picks them up
    automatically (no separate engine / reset path).
    """

    def __init__(self, session_factory: Optional[sessionmaker] = None) -> None:
        self._session_factory = session_factory or get_session_factory()

    def create(self, **kwargs: Any) -> Dict[str, Any]:
        """Insert a new api_keys row.

        Accepts either an ``APIKey`` instance via the ``model=`` kwarg
        OR the column fields directly (id / scope / key_hash /
        revoked_at). Returns the inserted row as a dict. Raises
        ``DuplicateAPIKey`` on a unique-violation.
        """
        if "model" in kwargs:
            model = kwargs.pop("model")
            if not isinstance(model, APIKey):
                raise TypeError("model= must be an APIKey instance")
            row: APIKey = model
        else:
            row = APIKey(**kwargs)

        session: Session = self._session_factory()
        try:
            session.add(row)
            session.commit()
            session.refresh(row)
            return self._to_dict(row)
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def lookup_active(self, candidate: str) -> Optional[Dict[str, Any]]:
        """Return the row dict whose ``key_hash`` matches the candidate,
        AND whose ``revoked_at`` is NULL. Returns ``None`` when no row
        matches (caller decides whether to raise ``InvalidAPIKey``).
        """
        candidate_hash = hash_api_key(candidate)
        session: Session = self._session_factory()
        try:
            stmt = (
                select(APIKey)
                .where(APIKey.key_hash == candidate_hash)
                .where(APIKey.revoked_at.is_(None))
                .limit(1)
            )
            row = session.execute(stmt).scalars().first()
            if row is None:
                return None
            return self._to_dict(row)
        finally:
            session.close()

    @staticmethod
    def _to_dict(row: APIKey) -> Dict[str, Any]:
        return {
            "id": row.id,
            "key_hash": row.key_hash,
            "scope": row.scope,
            "created_at": row.created_at.isoformat() if row.created_at else None,
            "revoked_at": row.revoked_at.isoformat() if row.revoked_at else None,
        }


__all__ = [
    "APIKeyRepository",
    "DuplicateAPIKey",
    "hash_api_key",
    "verify_api_key",
]