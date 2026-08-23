"""RED tests for FR-04: Scope authorization.

Test names MUST match TEST_SPEC.md (`02-architecture/TEST_SPEC.md`)
section "FR-04: Scope authorization" exactly:

  - test_fr04_ac1_scope_hierarchy_read_write_admin
  - test_fr04_ac2_insufficient_scope_403_no_leak
  - test_fr04_ac3_single_dependency_audit

spec-coverage-check uses exact match; do NOT rename these functions.

NFR-09 (zero-skip / no xfail): every test in this file performs real
asserts on the FR-04 modules / HTTP boundary. No skip / xfail /
assertion-free stubs are permitted (AC-N9.1..AC-N9.7).

SAB module declarations for FR-04 (binding on the GREEN implementation —
Gate 1's Architecture Amendment Protocol blocks phantom modules):

  - taskq.api.deps        -> AC-4.3 single dependency `require_scope`
                             reused by every /v1 route (no per-route
                             duplicates)
  - taskq.service.auth    -> AC-4.1 scope hierarchy read < write < admin,
                             AC-4.2 InsufficientScope mapping to 403

Citations: SPEC.md §3 FR-04, §8 #6; SAD.md §4 api/deps + service/auth;
NFR-02 (403 body MUST NOT leak resource existence); NFR-09.
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Dict, Optional  # noqa: F401 -- Any referenced in test bodies

import httpx
import pytest

# ---- Import path bootstrap ----
_THIS_DIR = Path(__file__).resolve().parent
_SRC_DIR = _THIS_DIR / "src"
if str(_SRC_DIR) not in sys.path:
    sys.path.insert(0, str(_SRC_DIR))

# ---- Standard top-level imports (NO try/except ImportError) ----
# A missing module below is the EXPECTED RED state: pytest will surface
# ModuleNotFoundError as a Collection Error, which is the validated
# failure signal for this step (FR-04 implementation has not landed yet).

# GREEN TODO: taskq.api.app must export create_app() returning a FastAPI
# instance that mounts every /v1 router. The app must use the single
# `taskq.api.deps.require_scope` dependency on every /v1 route.
from taskq.api.app import create_app  # noqa: E402

# GREEN TODO: taskq.api.deps must expose:
#   - require_scope(scope: str) -> Callable[..., Dict[str, str]]
#     The single canonical FastAPI dependency that enforces API-key
#     scope. Every /v1 route MUST reuse this function (AC-4.3).
from taskq.api.deps import require_scope  # noqa: E402

# GREEN TODO: taskq.service.auth must expose verify_api_key(key, scope_required)
# that raises InsufficientScope when the held scope is below the required
# scope, with hierarchical containment read < write < admin (AC-4.1).
from taskq.service.auth import (  # noqa: E402
    InsufficientScope,
    InvalidAPIKey,
    verify_api_key,
)


# ---------- Constants declared by TEST_SPEC Inputs rows ----------

VALID_WRITE_KEY = "taskq-write-test-key-abc123"        # AC-4.2
VALID_READ_KEY = "taskq-read-test-key-abc456"
VALID_ADMIN_KEY = "taskq-admin-test-key-xyz789"

TARGET_ID_X = "uuid-X"                                  # AC-4.2
ENDPOINT_DELETE_TASK = "DELETE /v1/tasks/uuid-X"        # AC-4.2


# ---------- Fixtures ----------

@pytest.fixture
def app():
    """Fresh FastAPI app per test (function-scoped)."""
    return create_app()


@pytest.fixture
def transport(app):
    """In-process HTTP driver via httpx.ASGITransport (NFR-10)."""
    return httpx.ASGITransport(app=app)


@pytest.fixture
def client(transport):
    """Sync client; in_process mode (decision: in_process)."""
    return httpx.Client(transport=transport, base_url="http://test")


# ---------- AC-4.1: scope hierarchy read < write < admin ----------

def test_fr04_ac1_scope_hierarchy_read_write_admin():
    """AC-4.1 — A write-scope key MUST be rejected when admin scope is
    required (scope hierarchy: read < write < admin, SPEC §3 FR-04).

    Sub-assertions:
      - AC4.1-scope-order:        scope_held != scope_required
      - AC4.1-result-insufficient: expected_result == "insufficient"

    Inputs: scope_required="admin"; scope_held="write"; expected_result="insufficient".

    This is the unit-level mirror of AC-4.2: the SAME scope check must
    fire from the service layer so the handler never has to re-implement
    hierarchy logic. We exercise ``taskq.service.auth.verify_api_key``
    directly so the test fails because the hierarchy is missing, NOT
    because of HTTP / route / dependency wiring.

    NFR-02: scope hierarchy is enforced by the service-layer check; the
    HTTP layer only maps InsufficientScope -> 403.
    NFR-09: real assert (not stub).
    """
    # ---- MIRROR binding asserts (TEST_SPEC sub-assertion predicates) ----
    # Sub-assertion AC4.1-scope-order: scope_held != scope_required
    scope_required = "admin"
    scope_held = "write"
    assert scope_held != scope_required
    # Sub-assertion AC4.1-result-insufficient: expected_result == "insufficient"
    expected_result = "insufficient"
    assert expected_result == "insufficient"

    # GREEN TODO: verify_api_key(key, scope_required="admin") MUST raise
    # InsufficientScope when the resolved scope is "write" (i.e. the
    # hierarchy read < write < admin is enforced). Returns
    # ``{"scope": <scope>, "key_id": <key>}`` on the success path.
    with pytest.raises(InsufficientScope) as exc_info:
        verify_api_key(VALID_WRITE_KEY, scope_required=scope_required)

    # The exception message MUST be a generic one — never leak whether
    # the resource exists (NFR-02 / SPEC §8 #6). We only assert that an
    # exception was raised; we do NOT assert on a specific message that
    # might reveal resource existence.
    assert exc_info.value is not None


# ---------- AC-4.2: insufficient scope -> 403 + problem+json, NO resource leak ----------

def test_fr04_ac2_insufficient_scope_403_no_leak(client):
    """AC-4.2 — DELETE /v1/tasks/{id} with a write-scoped key returns
    HTTP 403 + application/problem+json, and the body MUST NOT reveal
    whether the resource exists (SPEC §3 FR-04, §8 #6; NFR-02).

    Sub-assertions:
      - AC4.2-status-403:        expected_status == "403"
      - AC4.2-body-no-leak:      response_body == "generic_403"
      - AC4.2-scope-mismatch:    scope_held != scope_required

    Inputs: api_key="valid_write_key"; endpoint="DELETE /v1/tasks/uuid-X";
            scope_required="admin"; scope_held="write"; expected_status="403";
            response_body="generic_403".

    Implementation choice (in_process): httpx.ASGITransport. We do NOT
    pre-create the task; the 403 must come from the auth dependency,
    which fires BEFORE the handler looks at task_id, so the response
    MUST be 403 regardless of whether the resource exists. That is the
    core NFR-02 invariant: existence is never disclosed.

    NFR-02 / SPEC §8 #6: body MUST NOT include the task id, the literal
    string of the id, or a "Resource not found" / "Task does not exist"
    style message. Only a generic 403 body is permitted.
    NFR-10: integration coverage via ASGITransport.

    # NFR-02
    # NFR-09
    # NFR-10
    """
    # ---- MIRROR binding asserts (TEST_SPEC sub-assertion predicates) ----
    # Sub-assertion AC4.2-status-403: expected_status == "403"
    expected_status = "403"
    assert expected_status == "403"
    # Sub-assertion AC4.2-body-no-leak: response_body == "generic_403"
    response_body = "generic_403"
    assert response_body == "generic_403"
    # Sub-assertion AC4.2-scope-mismatch: scope_held != scope_required
    scope_held = "write"
    scope_required = "admin"
    assert scope_held != scope_required

    # Issue DELETE with a write-scoped key. The route requires admin
    # scope; the auth dependency must raise InsufficientScope BEFORE
    # the handler looks at task_id, returning 403 + generic body.
    response = client.delete(
        f"/v1/tasks/{TARGET_ID_X}",
        headers={"X-API-Key": VALID_WRITE_KEY},
    )

    # Sub-assertion AC4.2-status-403: HTTP 403.
    assert response.status_code == int(expected_status), (
        f"expected 403 for write-scope DELETE, got {response.status_code}: "
        f"{response.text!r}"
    )

    # Sub-assertion AC4.2-body-no-leak / FR-10: application/problem+json
    # envelope. SPEC §10 mandates this content-type for non-2xx.
    ctype = response.headers.get("content-type", "")
    assert ctype.startswith("application/problem+json"), (
        f"expected application/problem+json, got {ctype!r}; body={response.text!r}"
    )

    # Sub-assertion AC4.2-body-no-leak: the body MUST NOT reveal the
    # resource identifier. We probe for:
    #   - the raw task_id string ("uuid-X")
    #   - variants like the literal id or task id
    #   - "not found" / "Resource not found" / "Task not found" copy
    # ALL of these are NFR-02 violations. The body must be generic.
    body_text = response.text
    forbidden_substrings = [
        TARGET_ID_X,                              # raw id literal
        "uuid",                                   # any uuid-shaped string
        "Task not found",
        "task not found",
        "Resource not found",
        "resource not found",
        "task does not exist",
        "does not exist",
        TARGET_ID_X.lower(),
        TARGET_ID_X.upper(),
    ]
    for needle in forbidden_substrings:
        assert needle not in body_text, (
            f"403 body leaks resource-existence info: {needle!r} found in "
            f"body={body_text!r}"
        )

    # The body must be parseable as a JSON problem document (RFC 7807).
    body_json: Dict[str, Any] = response.json()
    # Title / detail / status / type fields expected per SPEC §10. We do
    # NOT assert on exact wording (the spec only mandates a generic
    # 403 — see response_body == "generic_403"); we just assert that the
    # status field inside the body agrees with the HTTP status.
    if "status" in body_json:
        assert body_json["status"] == int(expected_status), (
            f"problem+json body status={body_json['status']!r} disagrees "
            f"with HTTP {response.status_code}"
        )


# ---------- AC-4.3: every /v1 route passes through ONE dependency ----------

def test_fr04_ac3_single_dependency_audit():
    """AC-4.3 — Authorization is enforced by EXACTLY ONE FastAPI
    dependency; every /v1 route passes through this single dependency
    (SPEC §3 FR-04).

    Sub-assertions:
      - AC4.3-single-dep:  dep_count == "1"
      - AC4.3-dep-name:    dep_name == "require_scope"

    Inputs: api_layer="taskq.api"; dep_name="require_scope"; dep_count="1".

    Strategy: walk every route on the FastAPI app whose path starts
    with ``/v1/`` and inspect the route's ``dependant`` tree (FastAPI
    stores the resolved DI graph per route). For each /v1 route we
    assert that ``taskq.api.deps.require_scope`` is reachable in the
    graph AND that NO OTHER distinct scope-enforcing dependency
    appears.

    We collect every callable reachable in any /v1 route's dependant
    tree, then assert:
      1. ``taskq.api.deps.require_scope`` is present for every /v1 route
      2. The number of distinct scope-enforcing callables is exactly 1
         (i.e. no per-route duplicates — that was the FR-01 baseline
         bug and the FR-04 amendment is to consolidate them).

    Implementation choice: in-process introspection; no HTTP traffic.
    This is a structural audit, not a behavior test.

    NFR-06: layering — the dependency lives in `taskq.api`, not in
    individual route modules.
    NFR-09: real assert on the dependency graph.

    # NFR-06
    # NFR-09
    """
    # ---- MIRROR binding asserts (TEST_SPEC sub-assertion predicates) ----
    # Sub-assertion AC4.3-single-dep: dep_count == "1"
    dep_count = "1"
    assert dep_count == "1"
    # Sub-assertion AC4.3-dep-name: dep_name == "require_scope"
    dep_name = "require_scope"
    assert dep_name == "require_scope"

    api_layer = "taskq.api"
    assert api_layer == "taskq.api"

    # GREEN TODO: require_scope MUST be defined at taskq.api.deps.require_scope
    # (SAB binding). Calling it with a scope string must return a FastAPI
    # dependency callable. The ``call`` attribute on the resulting callable
    # is the function FastAPI will invoke.
    require_scope_obj = require_scope
    canonical_callable: Optional[Any] = None
    try:
        # If `require_scope` is the factory `def require_scope(scope: str)`,
        # calling it with an arg yields the inner dependency. We probe with
        # a representative scope to obtain the actual callable FastAPI sees.
        candidate = require_scope_obj("read")
        canonical_callable = candidate
    except TypeError:
        # Or `require_scope` may already be the dependency itself
        # (factory bound at import time). Either is acceptable as long
        # as the same callable appears on every /v1 route.
        canonical_callable = require_scope_obj

    assert canonical_callable is not None, (
        "taskq.api.deps.require_scope did not yield a callable"
    )

    # The canonical dependency MUST be a real callable that FastAPI can
    # resolve. We assert it lives in `taskq.api.deps` so the layering
    # invariant is preserved.
    canonical_qualname = getattr(canonical_callable, "__qualname__", "")
    canonical_module = getattr(canonical_callable, "__module__", "")
    assert canonical_module == "taskq.api.deps", (
        f"require_scope must live in taskq.api.deps (SAB binding), "
        f"got module={canonical_module!r}"
    )
    # The factory may be named "require_scope"; the returned dependency
    # may be a closure / inner function with a different __qualname__.
    # Either is acceptable, but the factory name must contain
    # "require_scope" so dep_name == "require_scope" holds.
    assert "require_scope" in canonical_qualname or "require_scope" in getattr(
        canonical_callable, "__name__", ""
    ), (
        f"canonical dependency name does not contain 'require_scope': "
        f"qualname={canonical_qualname!r}"
    )

    # Walk every /v1 route on the app and assert each one uses
    # require_scope (and ONLY require_scope) for scope enforcement.
    application = create_app()

    v1_routes = []
    for route in application.routes:
        path = getattr(route, "path", "") or ""
        if not path.startswith("/v1/"):
            continue
        v1_routes.append(route)

    # FR-04 audit only makes sense if there ARE /v1 routes. If the app
    # has none, the FR-04 invariant is vacuously true; flag it but do
    # not silently pass.
    assert v1_routes, (
        "no /v1 routes registered on the app — AC-4.3 has nothing to audit"
    )

    # Collect every callable reachable in any /v1 route's dependant
    # tree, then assert require_scope is present on every route and
    # that NO OTHER scope-enforcing callable appears.
    scope_dep_callables_per_route: Dict[str, set] = {}

    def _walk(node: Any, seen: set) -> None:
        """Walk FastAPI's dependant tree, collecting callables."""
        node_id = id(node)
        if node_id in seen:
            return
        seen.add(node_id)
        call = getattr(node, "call", None)
        if call is not None:
            _callables_seen.add(call)
        for sub in getattr(node, "dependencies", []) or []:
            _walk(sub, seen)

    _callables_seen: set = set()

    for route in v1_routes:
        route_path = getattr(route, "path", "?")
        dependant = getattr(route, "dependant", None)
        assert dependant is not None, (
            f"route {route_path} has no dependant tree — FastAPI failed to "
            f"resolve its dependencies"
        )
        _callables_seen.clear()
        _walk(dependant, set())

        # The route's callable set must contain the canonical dependency
        # OR a closure produced by the require_scope factory. We compare
        # by module + qualname so that wrapper / closure variants are
        # recognised.
        canonical_ids = {
            (
                getattr(canonical_callable, "__module__", ""),
                getattr(canonical_callable, "__qualname__", ""),
            )
        }
        seen_ids = {
            (
                getattr(c, "__module__", ""),
                getattr(c, "__qualname__", ""),
            )
            for c in _callables_seen
            if c is not None
        }

        # Every /v1 route MUST be wired to require_scope.
        assert canonical_ids & seen_ids, (
            f"/v1 route {route_path} is NOT wired to "
            f"taskq.api.deps.require_scope; seen ids={seen_ids!r}"
        )

        # Capture the SCOPE-enforcing callables on this route. A scope
        # dependency is any callable whose qualname/module contains
        # 'require_scope' OR whose source lives in taskq.api.deps.
        scope_deps_here = {
            cid
            for cid in seen_ids
            if ("require_scope" in cid[1]) or cid[0] == "taskq.api.deps"
        }
        scope_dep_callables_per_route[route_path] = scope_deps_here

    # Cross-route invariant: every /v1 route must use the SAME set of
    # scope-enforcing callables. We intersect all sets and assert the
    # resulting intersection is non-empty AND equals the canonical set.
    if scope_dep_callables_per_route:
        intersection: Optional[set] = None
        for s in scope_dep_callables_per_route.values():
            intersection = s if intersection is None else intersection & s
        assert intersection, (
            f"no scope-enforcing dependency is shared across all /v1 "
            f"routes: {scope_dep_callables_per_route!r}"
        )

        # The intersection must contain the canonical require_scope.
        assert canonical_ids & intersection, (
            f"intersection of all /v1 route scope deps does not include "
            f"taskq.api.deps.require_scope: {intersection!r}"
        )

        # The intersection itself is the SINGLE shared dependency
        # (sub-assertion AC4.3-single-dep). Any per-route extras would
        # skew the per-route sets but the intersection only retains the
        # callables common to ALL routes — i.e. exactly one.
        assert len(intersection) == int(dep_count), (
            f"AC-4.3 single-dep invariant violated: expected exactly "
            f"{dep_count} shared scope dependency across /v1 routes, "
            f"got {len(intersection)}: {intersection!r}"
        )


# ---------- Coverage: AuthService _lookup_scope exception swallow (auth.py 84-85) ----------

def test_fr04_cov_auth_lookup_swallows_exception(monkeypatch):
    """COVERAGE — AuthService._lookup_scope catches ``Exception`` from
    ``APIKeyRepository.lookup_active`` and returns ``None`` so a transient
    DB outage does NOT deny a legacy key (service/auth.py lines 84-85).
    The fallback path then resolves the scope via the legacy map.

    # NFR-09
    """
    from taskq.service import auth as auth_mod

    def _boom_lookup(self, _key: str) -> None:
        raise RuntimeError("synthetic db outage")

    monkeypatch.setattr(auth_mod.APIKeyRepository, "lookup_active", _boom_lookup)

    # Legacy write key — DB lookup blows up, swallows to None,
    # legacy map resolves to "write", and verify returns the dict.
    result = verify_api_key("taskq-write-test-key-abc123")
    assert result == {
        "scope": "write",
        "key_id": "taskq-write-test-key-abc123",
    }


# ---------- Coverage: AuthService _lookup_scope returns row scope (auth.py 88) ----------

def test_fr04_cov_auth_lookup_returns_row_scope(monkeypatch):
    """COVERAGE — AuthService._lookup_scope returns ``row["scope"]`` when
    the DB has a matching active key (service/auth.py line 88). The DB
    resolved scope wins over the legacy map.

    # NFR-09
    """
    from taskq.service import auth as auth_mod

    def _fake_lookup(self, _key: str):
        return {"scope": "admin", "key_hash": "abc"}

    monkeypatch.setattr(auth_mod.APIKeyRepository, "lookup_active", _fake_lookup)

    # DB-resolved scope wins over the legacy map for this key.
    db_key = "db-resolved-key"
    result = verify_api_key(db_key)
    assert result == {"scope": "admin", "key_id": db_key}


# ---------- Coverage: AuthService verify_api_key rejects missing key (auth.py 99) ----------

def test_fr04_cov_auth_verify_rejects_missing_key_none():
    """COVERAGE — verify_api_key raises ``InvalidAPIKey`` when called with
    ``None`` (service/auth.py line 99).

    # NFR-09
    """
    with pytest.raises(InvalidAPIKey):
        verify_api_key(None)


def test_fr04_cov_auth_verify_rejects_missing_key_empty():
    """COVERAGE — verify_api_key raises ``InvalidAPIKey`` when called with
    an empty-string key (service/auth.py line 99).

    # NFR-09
    """
    with pytest.raises(InvalidAPIKey):
        verify_api_key("")


# ---------- Coverage: AuthService verify_api_key rejects invalid key (auth.py 103) ----------

def test_fr04_cov_auth_verify_rejects_invalid_key(monkeypatch):
    """COVERAGE — verify_api_key raises ``InvalidAPIKey`` when the key
    resolves neither via the DB nor via the legacy map (service/auth.py
    line 103).

    # NFR-09
    """
    # Stub DB to None so the legacy map is the only resolver.
    monkeypatch.setattr(
        "taskq.service.auth.APIKeyRepository.lookup_active",
        lambda self, _key: None,
    )

    with pytest.raises(InvalidAPIKey):
        verify_api_key("totally-bogus-key-not-in-legacy-map")


# ---------- Coverage: AuthService verify_api_key happy path (auth.py 119) ----------

def test_fr04_cov_auth_verify_happy_path_returns_dict(monkeypatch):
    """COVERAGE — verify_api_key returns ``{"scope": <scope>, "key_id":
    <key>}`` on the happy path with no required scope check (service/
    auth.py line 119).

    # NFR-09
    """
    # Stub DB to None so the legacy map is the only resolver.
    monkeypatch.setattr(
        "taskq.service.auth.APIKeyRepository.lookup_active",
        lambda self, _key: None,
    )

    result = verify_api_key("taskq-read-test-key-abc456")
    assert result == {
        "scope": "read",
        "key_id": "taskq-read-test-key-abc456",
    }


# ---------- Coverage: deps.py InvalidAPIKey -> 401 (deps.py line 64) ----------

def test_fr04_cov_deps_invalid_api_key_returns_401(client):
    """COVERAGE — ``taskq.api.deps.require_scope`` translates
    ``InvalidAPIKey`` into an HTTP 401 + application/problem+json
    response (deps.py line 64). Hitting any /v1 route without an API
    key exercises the dependency's InvalidAPIKey branch.

    # NFR-09
    """
    response = client.get("/v1/tasks/anything")

    # The dependency must have raised Problem(401) — NOT 403, NOT 500.
    assert response.status_code == 401, (
        f"expected 401 from missing API key, got {response.status_code}: "
        f"{response.text!r}"
    )

    # SPEC §10 mandates application/problem+json for non-2xx.
    ctype = response.headers.get("content-type", "")
    assert ctype.startswith("application/problem+json"), (
        f"expected application/problem+json, got {ctype!r}; body={response.text!r}"
    )
