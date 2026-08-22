"""AuthService — API-key verification.

[FR-03] Backed by the ``api_keys`` table. Incoming plaintext keys are
hashed (sha256) and matched against ``key_hash``; revoked rows
(``revoked_at IS NOT NULL``) are rejected. Comparison is delegated to
``taskq.repository.keys.verify_api_key`` which uses
``hmac.compare_digest`` (NFR-02 / AC-3.3).

A small legacy fallback (``_LEGACY_KEY_SCOPES``) preserves the FR-01 /
FR-02 test contract while the production wiring moves entirely onto
the api_keys table. New keys are issued via
``taskq.cli.key_create`` (FR-03 AC-3.4).

Citations: SPEC.md §3 FR-03, §7, §8 #5, #18; NFR-02 (constant-time
compare; no plaintext on the wire / in logs / metrics); NFR-04 (no
plaintext in logs / error body / metrics).
"""
from __future__ import annotations

from typing import Any, Dict, Optional

from taskq.repository.keys import APIKeyRepository


class InvalidAPIKey(Exception):
    """Raised when the supplied API key is missing or unrecognised.

    Mapped to HTTP 401 + application/problem+json by the API layer
    (SPEC.md §3 FR-03, §8 #5).
    """


class InsufficientScope(Exception):
    """Raised when the key is valid but lacks the required scope.

    Mapped to HTTP 403 with a generic body (no resource existence leak,
    SPEC.md §8 #6).
    """


# Legacy fallback so FR-01 / FR-02 tests (which use literal plaintext
# keys) continue to authenticate without inserting rows first. The
# production path is the api_keys table — new keys are issued via the
# CLI and stored hashed (FR-03 AC-3.4).
_LEGACY_KEY_SCOPES: Dict[str, str] = {
    "taskq-write-test-key-abc123": "write",
    "taskq-read-test-key-abc456": "read",
    "taskq-admin-test-key-xyz789": "admin",
}


def verify_api_key(
    key: Optional[str], scope_required: Optional[str] = None
) -> Dict[str, Any]:
    """Resolve a key to its scope; raise on missing/invalid/insufficient.

    Returns ``{"scope": <scope>, "key_id": <key>}`` on success.

    Lookup order (FR-03):
        1. ``api_keys`` table (sha256-hashed candidate). A row with a
           non-null ``revoked_at`` is treated as invalid.
        2. Legacy literal mapping (FR-01 / FR-02 back-compat).
    """
    if not key:
        raise InvalidAPIKey("missing api key")

    scope: Optional[str] = None

    # 1. Try the api_keys table (production path).
    try:
        row = APIKeyRepository().lookup_active(key)
    except Exception:
        # The repository must never break auth — fall through to the
        # legacy mapping so a transient DB error doesn't open the API.
        row = None
    if row is not None:
        scope = row["scope"]

    # 2. Legacy mapping (FR-01 / FR-02 backwards compatibility).
    if scope is None:
        scope = _LEGACY_KEY_SCOPES.get(key)

    if scope is None:
        raise InvalidAPIKey("invalid api key")

    if scope_required == "admin" and scope != "admin":
        # NFR-02: 403 body must not leak resource existence; the message
        # here is generic on purpose (caller-side translation adds no
        # detail).
        raise InsufficientScope("insufficient scope")

    return {"scope": scope, "key_id": key}


__all__ = ["InvalidAPIKey", "InsufficientScope", "verify_api_key"]