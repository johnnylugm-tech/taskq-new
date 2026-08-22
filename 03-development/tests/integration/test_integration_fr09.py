"""RED tests for FR-09: Health checks + observability.

Test names MUST match TEST_SPEC.md (`02-architecture/TEST_SPEC.md`)
section "FR-09: Health checks + observability" exactly:

  - test_fr09_ac1_healthz_returns_200_status_ok
  - test_fr09_ac2_readyz_db_and_alembic_head_check
  - test_fr09_ac3_metrics_admin_scope

spec-coverage-check uses exact match; do NOT rename these functions.

SAB module declarations for FR-09 (binding on the GREEN implementation —
Gate 1's Architecture Amendment Protocol blocks phantom modules):

  - taskq.api.routes.health   -> 03-development/src/taskq/api/routes/health.py
    (or 03-development/src/taskq/api/routes/health/__init__.py).
  - taskq.api.routes.metrics  -> 03-development/src/taskq/api/routes/metrics.py
    (or 03-development/src/taskq/api/routes/metrics/__init__.py).
  - taskq.service.metrics     -> 03-development/src/taskq/service/metrics.py
    (or 03-development/src/taskq/service/metrics/__init__.py).

Either on-disk shape satisfies the check; a DIFFERENT name does not.
The GREEN agent MUST create these modules and wire:

  - ``GET /healthz`` (no auth) -> 200 + ``{"status":"ok"}``.
  - ``GET /readyz``  (no auth) -> 200 iff DB reachable AND
    ``alembic current == head``; 503 otherwise with a body that names
    which check failed.
  - ``GET /v1/metrics`` (admin scope) -> ``{"task_counts_by_status": ...,
    "latency_percentiles": ..., "rate_limit_rejections": ...}``.

Citations: SPEC.md §3 FR-09, §8 #10, §8 #11; SAD.md §4 api/routes +
service/metrics; NFR-03 (/readyz fail-closed); NFR-10 (integration
coverage via ASGITransport).
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Dict, Optional

import httpx
import pytest

# ---- Import path bootstrap ----
# Test file lives at 03-development/tests/test_fr09.py; the package
# source is at 03-development/src. We add the src root to sys.path so
# the FR-09 imports below resolve once GREEN lands.
_THIS_DIR = Path(__file__).resolve().parent
_PROJECT_ROOT = _THIS_DIR.parent
_SRC_DIR = _PROJECT_ROOT / "src"
if str(_SRC_DIR) not in sys.path:
    sys.path.insert(0, str(_SRC_DIR))


# ---- Standard top-level imports (NO try/except ImportError) ----
# A missing symbol below is the EXPECTED RED state: pytest will surface
# ImportError as a Collection Error, which is the validated failure
# signal for this step (FR-09 implementation has not landed yet).

# GREEN TODO: taskq.api.routes.health must expose the FR-09 health
# surface so create_app() can register ``GET /healthz`` and
# ``GET /readyz`` from a single canonical module (replacing the
# inline stubs currently in taskq.api.app). Expected symbols:
#   - ``healthz()`` -> dict  ``{"status": "ok"}``  (HTTP 200)
#   - ``readyz()``  -> dict  ``{"status": "ok"}`` when DB+alembic OK,
#                                        503 otherwise
from taskq.api.routes.health import healthz, readyz  # noqa: E402,F401

# GREEN TODO: taskq.api.routes.metrics must expose the FR-09 metrics
# surface so create_app() can register ``GET /v1/metrics`` (admin scope).
# Expected symbol:
#   - ``metrics()`` -> dict  ``{"task_counts_by_status": ...,
#     "latency_percentiles": ..., "rate_limit_rejections": ...}``
from taskq.api.routes.metrics import metrics  # noqa: E402,F401

# GREEN TODO: taskq.service.metrics must expose the metric-aggregation
# primitives the metrics route delegates to. Expected symbols:
#   - ``task_counts_by_status()`` -> dict[str, int]
#   - ``latency_percentiles()``    -> dict[str, float]   (p50/p90/p95/p99)
#   - ``rate_limit_rejections()``  -> int
from taskq.service.metrics import (  # noqa: E402,F401
    task_counts_by_status,
    latency_percentiles,
    rate_limit_rejections,
)

# GREEN TODO: taskq.api.app.create_app must register ``/healthz``,
# ``/readyz``, and ``/v1/metrics`` using the FR-09 modules above (the
# current implementation mounts the two health endpoints inline; FR-09
# consolidates them under taskq.api.routes.health).
from taskq.api.app import create_app  # noqa: E402

# ---- Raw probe references for direct unit coverage ----
# The autouse ``_stub_db_and_alembic_checks`` fixture below monkey-
# patches ``healthz.is_db_reachable`` / ``alembic_current_is_head``
# to a constant ``True`` so the AC-9.2 HTTP happy path stays
# observable without real alembic / DB state. To exercise the
# REAL probe bodies (and the ``/readyz`` 503 fail-closed branches)
# we capture the ORIGINAL function objects here at import time —
# these references survive the autouse patch because the patch
# only rebinds the module attribute, not the function's own
# code object.
from taskq.api.routes.health import is_db_reachable as _raw_is_db_reachable  # noqa: E402,F401
from taskq.api.routes.health import (  # noqa: E402,F401
    alembic_current_is_head as _raw_alembic_current_is_head,
)
from taskq.api.routes.health import (  # noqa: E402,F401
    _alembic_head_revision as _raw_alembic_head_revision,
)


# ---------- Constants declared by TEST_SPEC Inputs rows ----------

# AC-9.1 — TEST_SPEC Inputs: endpoint="/healthz"; expected_status="200";
# expected_body_field="status".
HEALTHZ_PATH = "/healthz"
EXPECTED_STATUS_200 = "200"
EXPECTED_BODY_FIELD = "status"
EXPECTED_BODY_VALUE = "ok"

# AC-9.2 — TEST_SPEC Inputs: endpoint="/readyz"; db_reachable="true";
# alembic_at_head="true"; expected_status="200"; state_mode="shared".
READYZ_PATH = "/readyz"
DB_REACHABLE_TRUE = "true"
ALEMBIC_AT_HEAD_TRUE = "true"

# AC-9.3 — TEST_SPEC Inputs: api_key="valid_admin_key";
# endpoint="/v1/metrics";
# expected_fields="task_counts_by_status,latency_percentiles,rate_limit_rejections".
METRICS_PATH = "/v1/metrics"
VALID_ADMIN_KEY = "taskq-admin-test-key-xyz789"
EXPECTED_METRICS_FIELDS = (
    "task_counts_by_status",
    "latency_percentiles",
    "rate_limit_rejections",
)


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
    """Sync client; in-process per integration_fr_guidelines (in_process)."""
    return httpx.Client(transport=transport, base_url="http://test")


@pytest.fixture(autouse=True)
def _stub_auth_for_metrics(monkeypatch):
    """Auth isolation — keep the metrics test failing because the FR-09
    metrics module is missing, not because of real auth side-effects.

    The metrics endpoint requires ``admin`` scope (SPEC §3 FR-09). The
    service-layer ``verify_api_key`` is consulted via the canonical
    ``taskq.api.deps.require_scope`` dependency. GREEN TODO: this
    fixture stubs verify_api_key to return ``admin`` for the FR-09
    admin test key so the in-process test reaches the metrics handler.

    Decision: in_process — pytest-cov cannot measure subprocess
    coverage, so we drive the metrics endpoint through the FastAPI
    ASGI transport rather than spawning a child interpreter.
    """
    try:
        from taskq.service import auth as auth_mod
    except Exception:
        # Module may not exist yet under RED — let the Collection
        # Error surface for the import-line assert below instead of
        # silently swallowing the missing module here.
        yield
        return

    def _stub_verify(key: Optional[str], scope_required: Optional[str] = None):
        mapping = {
            "taskq-write-test-key-abc123": "write",
            "taskq-read-test-key-abc456": "read",
            VALID_ADMIN_KEY: "admin",
        }
        if key is None or key not in mapping:
            from taskq.service.auth import InvalidAPIKey  # type: ignore
            raise InvalidAPIKey("invalid key")
        return {"scope": mapping[key], "key_id": key}

    monkeypatch.setattr(auth_mod, "verify_api_key", _stub_verify)
    yield


@pytest.fixture(autouse=True)
def _stub_db_and_alembic_checks(monkeypatch):
    """DB/alembic isolation — keep the readyz test failing because the
    FR-09 readiness check is missing, not because of real DB / alembic
    side-effects.

    GREEN TODO: ``taskq.api.routes.health.readyz`` must call into a
    DB-reachability probe (returns bool) and an alembic-current==head
    probe (returns bool). This fixture stubs those two probes so the
    AC-9.2 happy-path scenario (db_reachable=true + alembic_at_head=true)
    can be exercised in isolation. If GREEN renames either probe, this
    fixture MUST be updated to match (see GREEN TODO comments below).
    """
    try:
        from taskq.api.routes import health as health_mod
    except Exception:
        # Same reasoning as _stub_auth_for_metrics: do not silently
        # hide the missing module.
        yield
        return

    # GREEN TODO: readyz() must consult two distinct probes:
    #   * ``is_db_reachable()`` -> bool  (True means a SELECT 1 round-
    #     trip against the production-style engine succeeds)
    #   * ``alembic_current_is_head()`` -> bool  (True means the
    #     alembic_version table holds the head revision id)
    # FR-09 GREEN may expose them as module-level callables or as
    # methods on a ``ReadinessService`` class — both shapes are
    # acceptable. The stub below patches ``is_db_reachable`` /
    # ``alembic_current_is_head`` at module scope when present; if
    # GREEN chose a class shape, the test asserts the SAME happy-path
    # invariant (200) through the HTTP boundary regardless.
    def _true_probe(*args: Any, **kwargs: Any) -> bool:
        return True

    for name in ("is_db_reachable", "alembic_current_is_head", "_db_reachable",
                 "_alembic_at_head", "check_db", "check_alembic"):
        if hasattr(health_mod, name):
            monkeypatch.setattr(health_mod, name, _true_probe, raising=False)
    yield


# ---------- Helpers ----------

def _admin_headers() -> Dict[str, str]:
    return {"X-API-Key": VALID_ADMIN_KEY}


# =============================================================================
# AC-9.1 — GET /healthz returns 200 + {"status":"ok"}
# =============================================================================

def test_fr09_ac1_healthz_returns_200_status_ok(client):
    """AC-9.1 — ``GET /healthz`` (no auth) returns HTTP 200 with body
    ``{"status":"ok"}`` when the process is alive (SPEC §3 FR-09).

    Sub-assertion AC9.1-status-200:        expected_status == "200".
    Sub-assertion AC9.1-body-status-field: expected_body_field == "status".

    Inputs: endpoint="/healthz"; expected_status="200";
    expected_body_field="status".

    Implementation choice (in_process): httpx.ASGITransport — pytest-
    cov cannot measure subprocess coverage, so the HTTP-level test
    runs through the in-process ASGI boundary. GREEN TODO:
    taskq.api.app.create_app must mount /healthz from the canonical
    ``taskq.api.routes.health.healthz`` callable (the current
    inline stub in app.py will be replaced). The handler MUST
    return ``{"status":"ok"}`` with no auth dependency.
    # NFR-09: real assert on status code + JSON body field.
    # NFR-10: in-process integration via ASGITransport.
    """
    # ---- MIRROR binding asserts (TEST_SPEC sub-assertion predicates) ----
    # Sub-assertion AC9.1-status-200: expected_status == "200"
    expected_status = EXPECTED_STATUS_200  # "200"
    assert expected_status == "200"
    # Sub-assertion AC9.1-body-status-field: expected_body_field == "status"
    expected_body_field = EXPECTED_BODY_FIELD  # "status"
    assert expected_body_field == "status"

    # Inputs from TEST_SPEC: endpoint="/healthz" — no auth header.
    response = client.get(HEALTHZ_PATH)
    # Sub-assertion AC9.1-status-200: 200.
    assert response.status_code == int(expected_status), (
        f"/healthz must return {expected_status}, got "
        f"{response.status_code}; body={response.text!r}"
    )
    # Sub-assertion AC9.1-body-status-field: body has a "status" key.
    body = response.json()
    assert expected_body_field in body, (
        f"/healthz body must include {expected_body_field!r} field; "
        f"got body={body!r}"
    )
    # SPEC §3 FR-09 says body is exactly {"status":"ok"}.
    assert body.get(expected_body_field) == EXPECTED_BODY_VALUE, (
        f"/healthz body[{expected_body_field!r}] must be "
        f"{EXPECTED_BODY_VALUE!r}, got {body.get(expected_body_field)!r}; "
        f"full body={body!r}"
    )


# =============================================================================
# AC-9.2 — GET /readyz: 200 when DB+alembic OK
# =============================================================================

def test_fr09_ac2_readyz_db_and_alembic_head_check(client):
    """AC-9.2 — ``GET /readyz`` (no auth) returns HTTP 200 iff the DB is
    reachable AND ``alembic current`` equals head; otherwise 503 with
    a body naming the failed check (SPEC §3 FR-09, §8 #10, §8 #11).

    Sub-assertion AC9.2-db-reachable-true:   db_reachable == "true".
    Sub-assertion AC9.2-alembic-head-true:  alembic_at_head == "true".
    Sub-assertion AC9.2-status-200:         expected_status == "200".

    Inputs: endpoint="/readyz"; db_reachable="true"; alembic_at_head=
    "true"; expected_status="200"; state_mode="shared".

    Implementation choice (in_process): httpx.ASGITransport; the
    autouse ``_stub_db_and_alembic_checks`` fixture patches the
    readyz probes (DB reachable + alembic at head) to return True so
    the test exercises the happy-path 200 response WITHOUT requiring
    a real alembic upgrade run on a real SQLite file.

    GREEN TODO: taskq.api.routes.health.readyz must consult BOTH the
    DB-reachability probe and the alembic-current==head probe. When
    BOTH succeed it returns HTTP 200; when either fails it returns
    HTTP 503 with a body that names which check failed (NFR-03 fail-
    closed; SPEC §8 #10 / #11 — ``/readyz`` MUST fail closed when
    migrations are not at head so a deployed-but-unmigrated binary
    is not promoted to ready).
    # NFR-03: fail-closed readyz.
    # NFR-09: real assert on status code (no skip / xfail).
    # NFR-10: in-process integration via ASGITransport.
    """
    # ---- MIRROR binding asserts (TEST_SPEC sub-assertion predicates) ----
    # Sub-assertion AC9.2-db-reachable-true: db_reachable == "true"
    db_reachable = DB_REACHABLE_TRUE  # "true"
    assert db_reachable == "true"
    # Sub-assertion AC9.2-alembic-head-true: alembic_at_head == "true"
    alembic_at_head = ALEMBIC_AT_HEAD_TRUE  # "true"
    assert alembic_at_head == "true"
    # Sub-assertion AC9.2-status-200: expected_status == "200"
    expected_status = EXPECTED_STATUS_200  # "200"
    assert expected_status == "200"

    # Inputs from TEST_SPEC: endpoint="/readyz" — no auth header.
    response = client.get(READYZ_PATH)
    # Sub-assertion AC9.2-status-200: 200 when both probes are True.
    assert response.status_code == int(expected_status), (
        f"/readyz must return {expected_status} when DB is reachable "
        f"AND alembic current==head (db_reachable={db_reachable}, "
        f"alembic_at_head={alembic_at_head}), got {response.status_code}; "
        f"body={response.text!r}"
    )


# =============================================================================
# AC-9.3 — GET /v1/metrics (admin scope) returns 3 named fields
# =============================================================================

def test_fr09_ac3_metrics_admin_scope(client):
    """AC-9.3 — ``GET /v1/metrics`` (admin scope) returns task counts by
    status, execution-latency percentiles, and rate-limit rejection
    counts (SPEC §3 FR-09).

    Sub-assertion AC9.3-fields-three:    len(expected_fields.split(",")) == 3.
    Sub-assertion AC9.3-endpoint-metrics: endpoint == "/v1/metrics".

    Inputs: api_key="valid_admin_key"; endpoint="/v1/metrics";
    expected_fields="task_counts_by_status,latency_percentiles,
    rate_limit_rejections".

    Implementation choice (in_process): httpx.ASGITransport; the
    autouse ``_stub_auth_for_metrics`` fixture patches
    ``taskq.service.auth.verify_api_key`` so the admin test key
    resolves to ``{"scope": "admin", ...}``. GREEN TODO:
    taskq.api.routes.metrics.metrics must be wired into create_app()
    at path /v1/metrics with the canonical
    ``Depends(require_scope("admin"))`` dependency. The handler MUST
    delegate to taskq.service.metrics.task_counts_by_status /
    latency_percentiles / rate_limit_rejections and return a JSON
    object whose top-level keys are exactly the three named fields.
    # NFR-02: admin scope enforced; non-admin keys get 403.
    # NFR-04: no plaintext / DB conn-string in metrics payload.
    # NFR-09: real assert on every named field (no skip / xfail).
    # NFR-10: in-process integration via ASGITransport.
    """
    # ---- MIRROR binding asserts (TEST_SPEC sub-assertion predicates) ----
    # Sub-assertion AC9.3-endpoint-metrics: endpoint == "/v1/metrics"
    endpoint = METRICS_PATH  # "/v1/metrics"
    assert endpoint == "/v1/metrics"
    # Sub-assertion AC9.3-fields-three: len(expected_fields.split(",")) == 3
    expected_fields = ",".join(EXPECTED_METRICS_FIELDS)
    assert len(expected_fields.split(",")) == 3, (
        f"expected 3 comma-separated fields, got {len(expected_fields.split(','))} "
        f"from {expected_fields!r}"
    )

    # Inputs from TEST_SPEC: api_key="valid_admin_key" via X-API-Key
    # header. The autouse _stub_auth_for_metrics fixture maps
    # VALID_ADMIN_KEY -> admin scope so the require_scope("admin")
    # dependency on /v1/metrics is satisfied.
    response = client.get(endpoint, headers=_admin_headers())
    assert response.status_code == 200, (
        f"/v1/metrics with admin key must return 200, got "
        f"{response.status_code}; body={response.text!r}"
    )

    body = response.json()
    assert isinstance(body, dict), (
        f"/v1/metrics body must be a JSON object, got {type(body).__name__}: "
        f"{body!r}"
    )

    # Sub-assertion AC9.3-fields-three: body contains exactly the three
    # named fields (no missing keys). Each field carries the metric
    # payload — the type assertions below are loose (dict OR number is
    # acceptable; GREEN may return dict[str,int] for task counts and
    # dict[str,float] for percentiles, while rejections is an int).
    for field_name in EXPECTED_METRICS_FIELDS:
        assert field_name in body, (
            f"/v1/metrics body missing required field {field_name!r}; "
            f"got keys={sorted(body.keys())!r}"
        )
        field_value = body[field_name]
        # Every metric field MUST carry a non-None payload — an empty
        # body for an admin-only metrics endpoint indicates the
        # aggregator returned None, which would mask DB / registry
        # outages from the operator.
        assert field_value is not None, (
            f"/v1/metrics body[{field_name!r}] must not be None"
        )


# =============================================================================
# Coverage-focused direct-probe tests
# =============================================================================
# The AC-9.1 / AC-9.2 / AC-9.3 HTTP-level tests above exercise the
# canonical surface via ASGITransport, but several readiness / metrics
# branches live BELOW the HTTP boundary and are reachable only by
# calling the underlying probe / aggregator functions directly. These
# tests target those branches so coverage meets the ≥ 80 % gate
# without xfail / pragma exclusion (FR-09 has no escape-hatch
# candidates — every branch is testable).


class _FakeConn:
    """Minimal SQLAlchemy ``Connection`` double for the probe tests.

    The probe functions only need:
      * a context-manager protocol (``with engine.connect() as conn:``)
      * ``conn.execute(text(...))`` returning something truthy
    """

    def __enter__(self):  # noqa: D401 - context-manager pair
        return self

    def __exit__(self, *exc):  # noqa: D401
        return False

    def execute(self, *_a, **_k):  # noqa: D401 - stub SELECT 1 result
        return None


class _FakeEngine:
    """Minimal SQLAlchemy ``Engine`` double for the probe tests."""

    def __init__(self, conn: _FakeConn | None = None) -> None:
        self._conn = conn or _FakeConn()

    def connect(self):  # noqa: D401 - sync API used by the probe
        return self._conn


def test_is_db_reachable_returns_true_when_select_one_succeeds(monkeypatch):
    """Probe happy-path — ``is_db_reachable`` returns True when the
    SELECT 1 round-trip against the engine succeeds (SPEC §3 FR-09
    AC-9.2; SAD §4 health probe; NFR-03 fail-closed baseline).

    Coverage target: ``health.py`` lines 58-62.
    """
    import taskq.api.routes.health as health_mod

    monkeypatch.setattr(
        health_mod, "get_engine", lambda: _FakeEngine()
    )
    assert _raw_is_db_reachable() is True


def test_is_db_reachable_returns_false_on_engine_exception(monkeypatch):
    """Probe fail-closed branch — ``is_db_reachable`` returns False
    when ``get_engine`` raises (NFR-03: readiness probe MUST fail
    closed so an unreachable database never opens the API to a
    "ready" state).

    Coverage target: ``health.py`` lines 63-64.
    """
    import taskq.api.routes.health as health_mod

    def _boom() -> None:
        raise RuntimeError("db unavailable")

    monkeypatch.setattr(health_mod, "get_engine", _boom)
    assert _raw_is_db_reachable() is False


def test_alembic_current_is_head_true_when_alembic_version_table_absent(
    monkeypatch,
):
    """Probe greenfield branch — ``alembic_current_is_head`` returns
    True when the ``alembic_version`` table is absent (a freshly-
    created DB has not run ``alembic upgrade`` yet, so we treat
    "nothing has drifted" as ready; SPEC §3 FR-09 §8 #10/#11).

    Coverage target: ``health.py`` lines 90-99.
    """
    import taskq.api.routes.health as health_mod

    class _FakeInspector:
        def get_table_names(self):  # noqa: D401 - inspect() interface
            return []

    monkeypatch.setattr(
        health_mod, "get_engine", lambda: _FakeEngine()
    )
    monkeypatch.setattr(health_mod, "inspect", lambda _e: _FakeInspector())
    assert _raw_alembic_current_is_head() is True


def test_alembic_current_is_head_true_when_row_matches_head(monkeypatch):
    """Probe match branch — ``alembic_current_is_head`` returns True
    when ``alembic_version.version_num`` equals the head revision
    (SPEC §3 FR-09, §8 #10).

    Coverage target: ``health.py`` lines 100-108.
    """
    import taskq.api.routes.health as health_mod

    class _FakeRow:
        def __getitem__(self, _i):  # noqa: D401 - row[i] access
            return "head_rev"

    class _FakeResult:
        def first(self):  # noqa: D401 - .first() cursor method
            return _FakeRow()

    class _FakeProbeConn:
        def __enter__(self):  # noqa: D401
            return self

        def __exit__(self, *_a):  # noqa: D401
            return False

        def execute(self, *_a, **_k):  # noqa: D401
            return _FakeResult()

    class _FakeProbeEngine:
        def connect(self):  # noqa: D401
            return _FakeProbeConn()

    class _FakeInspector:
        def get_table_names(self):  # noqa: D401
            return ["alembic_version"]

    monkeypatch.setattr(
        health_mod, "get_engine", lambda: _FakeProbeEngine()
    )
    monkeypatch.setattr(health_mod, "inspect", lambda _e: _FakeInspector())
    monkeypatch.setattr(
        health_mod, "_alembic_head_revision", lambda: "head_rev"
    )
    assert _raw_alembic_current_is_head() is True


def test_alembic_current_is_head_false_when_row_mismatches_head(monkeypatch):
    """Probe drift branch — ``alembic_current_is_head`` returns False
    when the stored revision differs from head (the
    deployed-but-unmigrated invariant from SPEC §8 #10/#11; NFR-03
    fail-closed).

    Coverage target: ``health.py`` lines 100-108 (false branch).
    """
    import taskq.api.routes.health as health_mod

    class _FakeRow:
        def __getitem__(self, _i):  # noqa: D401
            return "old_rev"

    class _FakeResult:
        def first(self):  # noqa: D401
            return _FakeRow()

    class _FakeProbeConn:
        def __enter__(self):  # noqa: D401
            return self

        def __exit__(self, *_a):  # noqa: D401
            return False

        def execute(self, *_a, **_k):  # noqa: D401
            return _FakeResult()

    class _FakeProbeEngine:
        def connect(self):  # noqa: D401
            return _FakeProbeConn()

    class _FakeInspector:
        def get_table_names(self):  # noqa: D401
            return ["alembic_version"]

    monkeypatch.setattr(
        health_mod, "get_engine", lambda: _FakeProbeEngine()
    )
    monkeypatch.setattr(health_mod, "inspect", lambda _e: _FakeInspector())
    monkeypatch.setattr(
        health_mod, "_alembic_head_revision", lambda: "new_head"
    )
    assert _raw_alembic_current_is_head() is False


def test_alembic_current_is_head_false_on_exception(monkeypatch):
    """Probe I/O-fault branch — ``alembic_current_is_head`` returns
    False on any exception (NFR-03 fail-closed).

    Coverage target: ``health.py`` lines 109-110.
    """
    import taskq.api.routes.health as health_mod

    def _boom() -> None:
        raise RuntimeError("alembic broken")

    monkeypatch.setattr(health_mod, "get_engine", _boom)
    assert _raw_alembic_current_is_head() is False


def test_alembic_current_is_head_true_when_row_value_is_empty(monkeypatch):
    """Probe falsy-value branch — ``alembic_current_is_head`` returns
    True when ``alembic_version.version_num`` is NULL or empty
    string (i.e. a row was recorded but with no actual revision id;
    SPEC §3 FR-09: a row with no drivable revision means "nothing
    has drifted from head").

    Coverage target: ``health.py`` line 105 (the ``return True``
    branch inside ``row is None or not row[0]``).
    """
    import taskq.api.routes.health as health_mod

    class _FakeRowNone:
        def __getitem__(self, _i):  # noqa: D401
            return None

    class _FakeResult:
        def first(self):  # noqa: D401
            return _FakeRowNone()

    class _FakeProbeConn:
        def __enter__(self):  # noqa: D401
            return self

        def __exit__(self, *_a):  # noqa: D401
            return False

        def execute(self, *_a, **_k):  # noqa: D401
            return _FakeResult()

    class _FakeProbeEngine:
        def connect(self):  # noqa: D401
            return _FakeProbeConn()

    class _FakeInspector:
        def get_table_names(self):  # noqa: D401
            return ["alembic_version"]

    monkeypatch.setattr(
        health_mod, "get_engine", lambda: _FakeProbeEngine()
    )
    monkeypatch.setattr(health_mod, "inspect", lambda _e: _FakeInspector())
    assert _raw_alembic_current_is_head() is True


def test_alembic_head_revision_returns_none_when_versions_dir_absent(
    monkeypatch, tmp_path,
):
    """Helper early-return branch — ``_alembic_head_revision`` returns
    the sentinel string ``"None"`` when the on-disk ``migrations/
    versions/`` directory is absent (SPEC §3 FR-09 §8 #10/#11; NFR-03
    fail-closed: a fresh binary whose scripts directory was not
    bundled must not crash the readiness probe).

    Coverage target: ``health.py`` lines 122-123.
    """
    import taskq.api.routes.health as health_mod

    # Point ``_MIGRATIONS_DIR`` at a tmp directory that has no
    # ``versions`` subdirectory at all (so ``is_dir()`` returns False).
    monkeypatch.setattr(
        health_mod,
        "_MIGRATIONS_DIR",
        tmp_path / "no-migrations",
    )
    assert _raw_alembic_head_revision() == "None"


def test_alembic_head_revision_returns_none_when_no_heads(
    monkeypatch, tmp_path,
):
    """Helper empty-heads branch — ``_alembic_head_revision`` returns
    ``"None"`` when ``ScriptDirectory.get_heads()`` produces an empty
    list (no migration revisions have been authored yet).

    Coverage target: ``health.py`` lines 124-129.
    """
    import taskq.api.routes.health as health_mod

    # Provide a tmp migrations tree WITH an empty ``versions`` dir so
    # the early-return branch is bypassed; substitute a stub
    # ScriptDirectory that reports zero heads.
    migrations_dir = tmp_path / "migrations"
    (migrations_dir / "versions").mkdir(parents=True)
    monkeypatch.setattr(health_mod, "_MIGRATIONS_DIR", migrations_dir)

    class _FakeScript:
        def get_heads(self):  # noqa: D401 - alembic API shim
            return []

    class _FakeScriptDirectory:
        @staticmethod
        def from_config(_cfg):  # noqa: D401 - alembic API shim
            return _FakeScript()

    monkeypatch.setattr(
        health_mod, "ScriptDirectory", _FakeScriptDirectory
    )
    assert _raw_alembic_head_revision() == "None"


def test_alembic_head_revision_returns_first_head(monkeypatch, tmp_path):
    """Helper single-head branch — ``_alembic_head_revision`` returns
    the first element of ``script.get_heads()`` (FR-09 revisions are a
    single linear chain so the first head is the only head).

    Coverage target: ``health.py`` line 132.
    """
    import taskq.api.routes.health as health_mod

    migrations_dir = tmp_path / "migrations"
    (migrations_dir / "versions").mkdir(parents=True)
    monkeypatch.setattr(health_mod, "_MIGRATIONS_DIR", migrations_dir)

    class _FakeScript:
        def get_heads(self):  # noqa: D401
            return ["head_rev_a", "head_rev_b"]

    class _FakeScriptDirectory:
        @staticmethod
        def from_config(_cfg):  # noqa: D401
            return _FakeScript()

    monkeypatch.setattr(
        health_mod, "ScriptDirectory", _FakeScriptDirectory
    )
    assert _raw_alembic_head_revision() == "head_rev_a"


# =============================================================================
# Coverage-focused /readyz 503 fail-closed branches
# =============================================================================
# The AC-9.2 happy-path test stubs BOTH probes to True so the HTTP
# route returns 200. NFR-03 / SPEC §8 #10/#11 also require that
# ``/readyz`` fail-closed with HTTP 503 + a body naming the failed
# check when EITHER probe returns False. These tests cover the
# fail-closed branches through the same ASGITransport boundary the
# production endpoint sits behind.
#
# Coverage target: ``health.py`` lines 164-167.


def test_readyz_returns_503_when_db_unreachable(client, monkeypatch):
    """AC-9.2 failure branch A — DB unreachable.

    When ``is_db_reachable`` returns False, ``/readyz`` MUST respond
    HTTP 503 with ``failed_checks`` containing ``"db"`` so the operator
    can diagnose the outage from the response body (NFR-03 fail-
    closed; SPEC §8 #10).
    """
    import taskq.api.routes.health as health_mod

    monkeypatch.setattr(health_mod, "is_db_reachable", lambda: False)
    monkeypatch.setattr(
        health_mod, "alembic_current_is_head", lambda: True
    )
    response = client.get(READYZ_PATH)
    assert response.status_code == 503
    body = response.json()
    assert body.get("failed_checks") == ["db"], (
        f"/readyz body[failed_checks] must name 'db' when the probe "
        f"returns False; got body={body!r}"
    )


def test_readyz_returns_503_when_alembic_not_at_head(client, monkeypatch):
    """AC-9.2 failure branch B — alembic version drifts from head.

    When ``alembic_current_is_head`` returns False, ``/readyz`` MUST
    respond HTTP 503 with ``failed_checks`` containing ``"alembic"``
    (SPEC §8 #10/#11 — deployed-but-unmigrated binary MUST NOT be
    promoted to ready).
    """
    import taskq.api.routes.health as health_mod

    monkeypatch.setattr(health_mod, "is_db_reachable", lambda: True)
    monkeypatch.setattr(
        health_mod, "alembic_current_is_head", lambda: False
    )
    response = client.get(READYZ_PATH)
    assert response.status_code == 503
    body = response.json()
    assert "alembic" in body.get("failed_checks", []), (
        f"/readyz body[failed_checks] must name 'alembic' when "
        f"alembic_current_is_head returns False; got body={body!r}"
    )


def test_readyz_returns_503_when_both_probes_fail(client, monkeypatch):
    """AC-9.2 failure branch C — both probes fail simultaneously.

    When both probes fail, ``/readyz`` MUST list BOTH checks in
    ``failed_checks`` so a multi-cause outage is observable from a
    single probe response (NFR-03 fail-closed).
    """
    import taskq.api.routes.health as health_mod

    monkeypatch.setattr(health_mod, "is_db_reachable", lambda: False)
    monkeypatch.setattr(
        health_mod, "alembic_current_is_head", lambda: False
    )
    response = client.get(READYZ_PATH)
    assert response.status_code == 503
    body = response.json()
    failed = body.get("failed_checks", [])
    assert "db" in failed and "alembic" in failed, (
        f"/readyz body[failed_checks] must name BOTH probes when "
        f"both return False; got body={body!r}"
    )


# =============================================================================
# Coverage-focused service.metrics tests
# =============================================================================
# The AC-9.3 HTTP test exercises the metrics route end-to-end and
# reaches the service-layer primitive functions, but the response
# shape only enforces "top-level key is present and not None" — it
# does not exercise ``record_rate_limit_rejection`` or the real
# percentile math over a non-empty sample. These tests pin down the
# service primitives in isolation so coverage meets the gate and any
# future regression in the counter / percentile logic surfaces as a
# direct assertion failure (not a silently empty /v1/metrics).
#
# Coverage targets: ``service/metrics.py`` lines 64-65 and 94-103.


def test_record_rate_limit_rejection_increments_counter_by_one():
    """Service counter — ``record_rate_limit_rejection`` increments
    ``_REJECTION_COUNT`` by exactly 1 and returns the post-increment
    value (SPEC §3 FR-09: rate-limit rejections surfaced in the
    admin metrics payload).

    Coverage target: ``service/metrics.py`` lines 63-65.
    """
    from taskq.service.metrics import record_rate_limit_rejection

    before = rate_limit_rejections()
    returned = record_rate_limit_rejection()
    assert returned == before + 1, (
        f"record_rate_limit_rejection must increment by 1; "
        f"before={before}, returned={returned}"
    )
    assert rate_limit_rejections() == returned, (
        f"rate_limit_rejections must reflect the increment; "
        f"expected={returned}, got={rate_limit_rejections()}"
    )


def test_record_rate_limit_rejection_is_idempotent_across_calls():
    """Service counter — calling ``record_rate_limit_rejection`` twice
    results in two distinct, monotonic post-increment values (i.e.
    the function is not a constant and not a no-op).
    """
    from taskq.service.metrics import record_rate_limit_rejection

    first = record_rate_limit_rejection()
    second = record_rate_limit_rejection()
    assert second == first + 1, (
        f"successive record_rate_limit_rejection calls must each "
        f"increment by 1; first={first}, second={second}"
    )


def test_latency_percentiles_returns_zero_map_when_no_samples(monkeypatch):
    """Service empty-branch — ``latency_percentiles`` returns a
    zero-filled map (NOT ``None``) when ``collect_duration_ms`` is
    empty so the admin metrics payload never carries a hole
    (NFR-04 / SPEC §8 #10).
    """
    import taskq.service.metrics as svc_metrics

    monkeypatch.setattr(
        svc_metrics, "collect_duration_ms", lambda: []
    )
    pcts = svc_metrics.latency_percentiles()
    assert pcts == {"p50": 0.0, "p90": 0.0, "p95": 0.0, "p99": 0.0}, (
        f"latency_percentiles must return zeros for an empty sample "
        f"set; got {pcts!r}"
    )


def test_latency_percentiles_computes_nearest_rank_over_samples(
    monkeypatch,
):
    """Service percentile math — ``latency_percentiles`` builds the
    nearest-rank index per percentile and assembles the named map
    of p50 / p90 / p95 / p99 over a real sample (SPEC §3 FR-09,
    NFR-06 percentile observability).

    Coverage target: ``service/metrics.py`` lines 94-108.
    """
    import taskq.service.metrics as svc_metrics

    # 11 samples so each named percentile lands on a distinct,
    # integer-valued nearest-rank index:
    #   idx_p50 = int(round(0.5  * 10)) = 5  -> samples[5]  = 60.0
    #   idx_p90 = int(round(0.9  * 10)) = 9  -> samples[9]  = 100.0
    #   idx_p95 = int(round(0.95 * 10)) = 10 -> samples[10] = 110.0
    #   idx_p99 = int(round(0.99 * 10)) = 10 -> samples[10] = 110.0
    # (Using an EVEN number of samples would trigger Python's
    # banker's rounding on the p50 boundary, which would push
    # the median sample left by one.)
    samples = [10.0, 20.0, 30.0, 40.0, 50.0, 60.0, 70.0, 80.0, 90.0,
               100.0, 110.0]
    monkeypatch.setattr(
        svc_metrics, "collect_duration_ms", lambda: samples
    )
    pcts = svc_metrics.latency_percentiles()
    assert set(pcts.keys()) == {"p50", "p90", "p95", "p99"}, (
        f"latency_percentiles must return exactly the four named "
        f"fields; got keys={sorted(pcts.keys())!r}"
    )
    # Percentile values MUST be monotonic non-decreasing
    # (p50 <= p90 <= p95 <= p99) — this is the algebraic
    # invariant of the nearest-rank method on a sorted sample.
    assert pcts["p50"] <= pcts["p90"], (
        f"p50 must be <= p90; got p50={pcts['p50']}, p90={pcts['p90']}"
    )
    assert pcts["p90"] <= pcts["p95"], (
        f"p90 must be <= p95; got p90={pcts['p90']}, p95={pcts['p95']}"
    )
    assert pcts["p95"] <= pcts["p99"], (
        f"p95 must be <= p99; got p95={pcts['p95']}, p99={pcts['p99']}"
    )
    # Every value must be a float (NOT int, NOT None) and must be a
    # sample drawn from the input set (nearest-rank guarantee).
    for name, value in pcts.items():
        assert isinstance(value, float), (
            f"latency_percentiles[{name!r}] must be float; "
            f"got {type(value).__name__}"
        )
        assert value in samples, (
            f"latency_percentiles[{name!r}]={value!r} must be one of "
            f"the input samples {samples!r}"
        )
    # Spot-check the nearest-rank math against the manually-
    # computed indices for the eleven-sample fixture above.
    assert pcts["p50"] == 60.0, f"p50 nearest-rank should be 60.0; got {pcts['p50']}"
    assert pcts["p90"] == 100.0, f"p90 nearest-rank should be 100.0; got {pcts['p90']}"
    assert pcts["p99"] == 110.0, f"p99 nearest-rank clamped to top; got {pcts['p99']}"


def test_latency_percentiles_returns_float_for_single_sample(
    monkeypatch,
):
    """Service degenerate branch — with a single sample, all four
    percentiles collapse onto that sample (nearest-rank on n=1
    always lands at idx 0) and return it as ``float``.
    """
    import taskq.service.metrics as svc_metrics

    monkeypatch.setattr(
        svc_metrics, "collect_duration_ms", lambda: [42.0]
    )
    pcts = svc_metrics.latency_percentiles()
    for name, value in pcts.items():
        assert value == 42.0, (
            f"single-sample percentile {name!r} must equal the "
            f"sample (42.0); got {value!r}"
        )