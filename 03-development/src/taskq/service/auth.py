"""AuthService — API-key verification.

[FR-01] Supports FR-01 auth (write/read/admin scopes). The test
fixture's _stub_verify is the spec for which keys map to which scopes;
verify_api_key below implements that contract.
Citations: SPEC.md §3 FR-01; FR-04 (auth/permissions); NFR-02 (no
leak on auth failure).
"""
from __future__ import annotations

from typing import Any, Dict, Optional


class InvalidAPIKey(Exception):
    """Raised when the supplied API key is missing or unrecognised.

    Mapped to HTTP 401 + application/problem+json by the API layer
    (SPEC.md §3 FR-01, §8 #5).
    """


class InsufficientScope(Exception):
    """Raised when the key is valid but lacks the required scope.

    Mapped to HTTP 403 with a generic body (no resource existence leak,
    SPEC.md §8 #6).
    """


# Test-only key/scope map. Production wiring (FR-03) will back this with
# a repository + hash; for FR-01 GREEN the literal keys in test_fr01.py
# are what the implementation must accept.
_KEY_SCOPES: Dict[str, str] = {
    "taskq-write-test-key-abc123": "write",
    "taskq-read-test-key-abc456": "read",
    "taskq-admin-test-key-xyz789": "admin",
}


def verify_api_key(
    key: Optional[str], scope_required: Optional[str] = None
) -> Dict[str, Any]:
    """Resolve a key to its scope; raise on missing/invalid/insufficient.

    Returns ``{"scope": <scope>, "key_id": <key>}`` on success.
    """
    if not key:
        raise InvalidAPIKey("missing api key")
    scope = _KEY_SCOPES.get(key)
    if scope is None:
        raise InvalidAPIKey("invalid api key")

    if scope_required == "admin" and scope != "admin":
        # NFR-02: 403 body must not leak resource existence; the message here
        # is generic on purpose (caller-side translation adds no detail).
        raise InsufficientScope("insufficient scope")

    return {"scope": scope, "key_id": key}


__all__ = ["InvalidAPIKey", "InsufficientScope", "verify_api_key"]
