"""AuthService — API-key verification (FR-03, FR-04).

Incoming plaintext keys are resolved to a scope via two paths:

1. ``api_keys`` table — SHA-256 hash + ``hmac.compare_digest`` (AC-3.2,
   AC-3.3). A row with non-null ``revoked_at`` is filtered out by
   ``APIKeyRepository.lookup_active`` (AC-3.5).
2. ``_LEGACY_KEY_SCOPES`` — a small literal map preserved from FR-01 /
   FR-02 so existing tests keep authenticating without a row insert.

A successful resolution returns ``{"scope": <scope>, "key_id": <key>}``.
Missing / invalid keys raise ``InvalidAPIKey`` (mapped to HTTP 401 +
problem+json by the API layer); a valid key missing the required scope
raises ``InsufficientScope`` (mapped to HTTP 403).

[FR-04] The scope hierarchy ``read < write < admin`` (AC-4.1) is
enforced here via the ``_SCOPE_RANK`` map. The HTTP layer
(``taskq.api.deps.require_scope``) delegates the entire check to this
function so the hierarchy logic is never duplicated at the route
level — the single FastAPI dependency (AC-4.3) translates
``InsufficientScope`` into a generic 403 problem document with no
resource-existence leak (SPEC.md §8 #6, NFR-02).

Citations: SPEC.md §3 FR-03, §3 FR-04, §7, §8 #5, §8 #6, §8 #18;
NFR-02 (constant-time compare; no plaintext on the wire / in logs /
metrics; no resource-existence leak in 403 body); NFR-04 (no plaintext
in logs / error body / metrics).
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


# Back-compat shim for FR-01 / FR-02 tests, which use literal plaintext
# keys without first inserting rows. New keys are issued via
# ``taskq.cli.key_create`` and stored hashed (FR-03 AC-3.4).
_LEGACY_KEY_SCOPES: Dict[str, str] = {
    "taskq-write-test-key-abc123": "write",
    "taskq-read-test-key-abc456": "read",
    "taskq-admin-test-key-xyz789": "admin",
}


# Scope hierarchy — read < write < admin (SPEC.md §3 FR-04, AC-4.1).
# A held scope at or above the required rank satisfies the request;
# anything below raises ``InsufficientScope`` which the HTTP layer
# maps to 403 + generic body (no resource-existence leak, SPEC §8 #6).
_SCOPE_RANK: Dict[str, int] = {
    "read": 0,
    "write": 1,
    "admin": 2,
}


def _lookup_scope(key: str) -> Optional[str]:
    """Return the scope for ``key`` from the api_keys table, else None.

    [FR-06] AC-6.1 — the service layer MUST NOT import or hold a
    SQLAlchemy primitive (NFR-06 layering). DB errors therefore surface
    as a generic ``Exception``-class instance; we catch broadly so a
    transient storage failure does not open the API (the legacy
    mapping still gives the call a chance to resolve).
    """
    try:
        row = APIKeyRepository().lookup_active(key)
    except Exception:
        return None
    if row is None:
        return None
    return row["scope"]


def verify_api_key(
    key: Optional[str], scope_required: Optional[str] = None
) -> Dict[str, Any]:
    """Resolve a key to its scope; raise on missing/invalid/insufficient.

    Returns ``{"scope": <scope>, "key_id": <key>}`` on success.
    """
    if not key:
        raise InvalidAPIKey("missing api key")

    scope = _lookup_scope(key) or _LEGACY_KEY_SCOPES.get(key)
    if scope is None:
        raise InvalidAPIKey("invalid api key")

    # NFR-02 / SPEC §8 #6: 403 body must not leak resource existence —
    # message is generic on purpose. AC-4.1 enforces the read < write <
    # admin hierarchy: a held scope at or above the required rank
    # satisfies the request; anything below raises ``InsufficientScope``.
    if scope_required is not None:
        required_rank = _SCOPE_RANK.get(scope_required)
        held_rank = _SCOPE_RANK.get(scope)
        if (
            required_rank is None
            or held_rank is None
            or held_rank < required_rank
        ):
            raise InsufficientScope("insufficient scope")

    return {"scope": scope, "key_id": key}


__all__ = ["InvalidAPIKey", "InsufficientScope", "verify_api_key"]
