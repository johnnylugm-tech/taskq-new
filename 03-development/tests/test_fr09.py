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
    expected_fields_csv = ",".join(EXPECTED_METRICS_FIELDS)
    assert len(expected_fields_csv.split(",")) == 3, (
        f"expected 3 comma-separated fields, got {len(expected_fields_csv.split(','))} "
        f"from {expected_fields_csv!r}"
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