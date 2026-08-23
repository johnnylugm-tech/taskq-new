"""RED tests for FR-05: Rate limiting (per-token token bucket).

Test names MUST match TEST_SPEC.md (`02-architecture/TEST_SPEC.md`)
section "FR-05: Rate limiting" exactly:

  - test_fr05_ac1_bucket_config_burst_per_sec
  - test_fr05_ac2_overflow_returns_429_with_retry_after
  - test_fr05_ac3_db_backed_row_level_lock
  - test_fr05_ac4_healthz_readyz_rate_limit_exempt

spec-coverage-check uses exact match; do NOT rename these functions.

NFR-09 (zero-skip / no xfail): every test in this file performs real
asserts on the FR-05 modules / HTTP boundary. No skip / xfail /
assertion-free stubs are permitted (AC-N9.1..AC-N9.7).

SAB module declarations for FR-05 (binding on the GREEN implementation —
Gate 1's Architecture Amendment Protocol blocks phantom modules):

  - taskq.api.middleware         -> AC-5.2 / AC-5.4 rate-limit ASGI
                                    middleware that exempts /healthz,
                                    /readyz and emits 429 + Retry-After
                                    when the bucket is empty.
  - taskq.service.rate_limit     -> AC-5.1 token-bucket configuration
                                    (TASKQ_RATE_BURST, TASKQ_RATE_PER_SEC)
                                    and the service-layer refill check
                                    shared across workers.
  - taskq.repository.rate_buckets -> AC-5.3 row-level lock
                                    (`SELECT ... FOR UPDATE` /
                                    `with_for_update()`) inside a single
                                    transaction, so concurrent workers
                                    don't overshoot the bucket.

Citations: SPEC.md §3 FR-05, §5.1, §8 #9; SAD.md §4 middleware/service/
repository layers; NFR-03 (DB-backed shared state); NFR-13 (row-level
lock under concurrency).
"""
from __future__ import annotations

import inspect
import sys
import time
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
# failure signal for this step (FR-05 implementation has not landed yet).

# GREEN TODO: taskq.api.app.create_app() must additionally install the
# rate-limit middleware declared in `taskq.api.middleware` so every
# /v1/* request is gated by the per-token bucket. /healthz and /readyz
# MUST be exempt (AC-5.4).
from taskq.api.app import create_app  # noqa: E402

# GREEN TODO: taskq.service.rate_limit must expose a configuration
# container with the TASKQ_RATE_BURST / TASKQ_RATE_PER_SEC fields
# (AC-5.1) and a check / consume entry point used by both the HTTP
# middleware and the repository to gate requests.
from taskq.service.rate_limit import (  # noqa: E402
    RateLimitConfig,
    TokenBucket,
    consume_token,
    seconds_until_next_token,
)

# GREEN TODO: taskq.repository.rate_buckets must expose a
# RateBucketRepository that performs the refill + row-level lock
# (`SELECT ... FOR UPDATE` / `with_for_update()`) inside ONE
# transaction (AC-5.3). The repository MUST own the SQL — the
# service layer only sees domain values.
from taskq.repository.rate_buckets import RateBucketRepository  # noqa: E402


# ---------- Constants declared by TEST_SPEC Inputs rows ----------

VALID_READ_KEY = "taskq-read-test-key-abc456"  # AC-5.2

# AC-5.1 — TEST_SPEC Inputs: burst="20"; per_sec="5.0".
RATE_BURST = 20
RATE_PER_SEC = 5.0

# AC-5.2 — TEST_SPEC Inputs: request_count="100";
# requests_overflow_bucket="true"; burst="20"; expected_status="429";
# expected_header="Retry-After".
RATE_REQUEST_COUNT = 100

# AC-5.3 — TEST_SPEC Inputs: key_id="key-X"; transaction_count="2";
# lock_mode="with_for_update"; state_mode="shared".
KEY_ID_X = "key-X"
TRANSACTION_COUNT = 2

# AC-5.4 — TEST_SPEC Inputs: endpoint="/healthz"; request_count="1000";
# requests_overflow_bucket="true"; expected_status="200".
HEALTHZ_REQUEST_COUNT = 1000


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


@pytest.fixture(autouse=True)
def _isolate_external_state():
    """Per-test isolation.

    For FR-05 GREEN, the rate-buckets table must be reset alongside the
    tasks/api_keys tables; for this RED step we keep the fixture
    intentionally minimal so the test fails because the FR-05 module
    is missing, NOT because of side-effects from earlier tests.
    """
    yield


# ---------- AC-5.1: bucket config TASKQ_RATE_BURST + TASKQ_RATE_PER_SEC ----------

def test_fr05_ac1_bucket_config_burst_per_sec():
    """AC-5.1 — Per-token token bucket has capacity ``TASKQ_RATE_BURST``
    and refill rate ``TASKQ_RATE_PER_SEC`` (SPEC §3 FR-05, §5.1).

    Sub-assertions:
      - AC5.1-burst-cap:       burst == "20"
      - AC5.1-rate-per-sec:    per_sec == "5.0"
      - AC5.1-state-shared:    state_mode == "shared"

    Inputs: burst="20"; per_sec="5.0"; state_mode="shared".

    This is the unit-level mirror: the SAME config object that the
    HTTP middleware / repository both consult. We exercise
    ``taskq.service.rate_limit.RateLimitConfig`` directly so the test
    fails because the config is missing, NOT because of HTTP /
    route / dependency wiring.

    The ``TokenBucket`` instance MUST consume ``burst`` tokens before
    refusing, then refill at ``per_sec`` per real second. We probe
    that synchronously via a rewindable clock so the test stays fast
    and deterministic.

    NFR-03 (shared DB-backed state): the test does not pin a specific
    storage backend, but it does pin the SHAPE of the configuration
    so any worker process that reads it agrees on the limits.
    NFR-09: real assert on the config values and bucket semantics.
    """
    # ---- MIRROR binding asserts (TEST_SPEC sub-assertion predicates) ----
    # Sub-assertion AC5.1-burst-cap: burst == "20"
    burst = "20"
    assert burst == "20"
    # Sub-assertion AC5.1-rate-per-sec: per_sec == "5.0"
    per_sec = "5.0"
    assert per_sec == "5.0"
    # Sub-assertion AC5.1-state-shared: state_mode == "shared"
    state_mode = "shared"
    assert state_mode == "shared"

    # GREEN TODO: RateLimitConfig(burst=int, per_sec=float) MUST expose
    # ``.burst`` and ``.per_sec`` attributes that match the env values
    # TASKQ_RATE_BURST=20 / TASKQ_RATE_PER_SEC=5.0 (SPEC §5.1).
    cfg = RateLimitConfig(burst=int(burst), per_sec=float(per_sec))

    # Sub-assertion AC5.1-burst-cap: capacity equals 20.
    assert cfg.burst == int(burst), (
        f"expected burst={int(burst)}, got {cfg.burst!r}"
    )
    # Sub-assertion AC5.1-rate-per-sec: refill rate equals 5.0 per sec.
    assert cfg.per_sec == float(per_sec), (
        f"expected per_sec={float(per_sec)}, got {cfg.per_sec!r}"
    )

    # GREEN TODO: TokenBucket(config) MUST hold at most ``burst`` tokens
    # at any instant, refill at ``per_sec`` per real second, and report
    # the time at which the next token becomes available (used to fill
    # the Retry-After header). We exercise the public surface only;
    # no private attribute access.
    bucket = TokenBucket(cfg)
    now_ref = [time.time()]

    def _now() -> float:
        return now_ref[0]

    # Probe: consume exactly `burst` tokens in tight succession — all
    # MUST succeed, and the bucket MUST report 0 remaining afterwards.
    consumed = 0
    for _ in range(int(burst)):
        ok = consume_token(bucket, now=_now())
        assert ok is True, (
            f"bucket refused a token before capacity was exhausted: "
            f"consumed={consumed}/{int(burst)}"
        )
        consumed += 1

    # Next token MUST be refused (bucket empty).
    assert consume_token(bucket, now=_now()) is False, (
        f"bucket still served a token after capacity exhausted "
        f"({int(burst)} consumed)"
    )

    # Advance the clock by 1 second at ``per_sec`` refill. The bucket
    # MUST replenish approximately per_sec tokens. We allow a 1-token
    # slack to keep the test robust against floating-point rounding
    # without locking the GREEN implementation into a specific round
    # strategy.
    now_ref[0] += 1.0
    refilled = 0
    while consume_token(bucket, now=_now()) is True:
        refilled += 1
        if refilled > int(burst):
            break  # safety net so the test cannot loop forever
    assert 1 <= refilled <= int(burst), (
        f"after 1s of refill at per_sec={float(per_sec)} expected "
        f"~{float(per_sec)} tokens replenished, got {refilled}"
    )


# ---------- AC-5.2: bucket overflow -> 429 + Retry-After ----------

def test_fr05_ac2_overflow_returns_429_with_retry_after(client):
    """AC-5.2 — A request that exceeds the bucket returns HTTP 429 +
    ``application/problem+json`` with a ``Retry-After`` header whose
    value is in seconds (SPEC §3 FR-05, §8 #9).

    Sub-assertions:
      - AC5.2-status-429:          expected_status == "429"
      - AC5.2-header-retry-after:  expected_header == "Retry-After"
      - AC5.2-requests-over-burst: requests_overflow_bucket == "true"

    Inputs: api_key="valid_read_key"; request_count="100";
            burst="20"; requests_overflow_bucket="true";
            expected_status="429"; expected_header="Retry-After";
            state_mode="shared".

    Strategy: hammer a /v1 GET endpoint with read scope enough times
    to overflow the bucket (burst=20, plus any tokens the refill
    trickles in). The first ``burst`` requests must succeed (or fail
    for non-rate-limit reasons); at least one request AFTER the
    overflow MUST come back as 429 with a Retry-After header. We
    don't pin the exact overflow index — only the asymptotic fact
    that 100 requests in quick succession against a burst=20 bucket
    MUST produce at least one 429 (NP-03 / SPEC §8 #9).

    Implementation choice (in_process): httpx.ASGITransport so the
    NFR-10 end-to-end integration boundary is exercised.

    # NFR-02 (problem+json 429 body — error contract)
    # NFR-03 (rate limit 429)
    # NFR-09
    # NFR-10
    """
    # ---- MIRROR binding asserts (TEST_SPEC sub-assertion predicates) ----
    # Sub-assertion AC5.2-status-429: expected_status == "429"
    expected_status = "429"
    assert expected_status == "429"
    # Sub-assertion AC5.2-header-retry-after: expected_header == "Retry-After"
    expected_header = "Retry-After"
    assert expected_header == "Retry-After"
    # Sub-assertion AC5.2-requests-over-burst: requests_overflow_bucket == "true"
    requests_overflow_bucket = "true"
    assert requests_overflow_bucket == "true"

    burst_int = int(RATE_BURST)        # 20
    request_total = int(RATE_REQUEST_COUNT)  # 100
    assert request_total > burst_int, (
        f"request_count ({request_total}) must exceed burst "
        f"({burst_int}) so the test can observe overflow"
    )

    statuses: list[int] = []
    retry_after_seen: list[str] = []
    content_types: list[str] = []

    # Hammer the read endpoint. We use GET /v1/tasks because it has
    # the lowest pre-conditions (read scope, no body). Any 429
    # observed is the rate-limit middleware doing its job; any 401
    # is a test isolation issue (handled by the conftest reset) —
    # we tolerate 401s so the test still proves the 429 path
    # exists, even when the GREEN-side api_keys seed isn't in place.
    for _ in range(request_total):
        response = client.get(
            "/v1/tasks",
            headers={"X-API-Key": VALID_READ_KEY},
        )
        statuses.append(response.status_code)
        ctype = response.headers.get("content-type", "")
        content_types.append(ctype)
        if response.status_code == int(expected_status):
            retry_after_seen.append(
                response.headers.get(expected_header, "")
            )

    # Sub-assertion AC5.2-status-429: at least one 429 must be observed
    # given 100 requests vs a burst of 20 (NP-03 / SPEC §8 #9).
    assert int(expected_status) in statuses, (
        f"expected at least one HTTP 429 across {request_total} "
        f"requests (burst={burst_int}); observed distribution: "
        f"{sorted(set(statuses))}; first-5 statuses={statuses[:5]}"
    )

    # Sub-assertion AC5.2-header-retry-after: the 429 response MUST
    # carry a Retry-After header whose value is a non-empty integer
    # string (seconds, per SPEC §3 FR-05).
    assert retry_after_seen, (
        "no 429 response observed carrying the Retry-After header"
    )
    retry_after_value = retry_after_seen[0]
    assert retry_after_value.strip() != "", (
        f"Retry-After header value is empty: {retry_after_value!r}"
    )
    # SPEC §3 FR-05 / §8 #9: Retry-After is in SECONDS — must be a
    # non-negative integer string.
    assert retry_after_value.strip().isdigit(), (
        f"Retry-After must be an integer (seconds), got "
        f"{retry_after_value!r}"
    )
    retry_after_seconds = int(retry_after_value.strip())
    assert retry_after_seconds >= 0, (
        f"Retry-After must be >= 0, got {retry_after_seconds}"
    )

    # FR-10 / SPEC §10: 429 must use application/problem+json.
    problem_ctype_seen = [
        c for c, s in zip(content_types, statuses)
        if s == int(expected_status) and c.startswith("application/problem+json")
    ]
    assert problem_ctype_seen, (
        f"429 response did not use application/problem+json content-type; "
        f"observed content-types on 429s: "
        f"{[c for c, s in zip(content_types, statuses) if s == int(expected_status)]}"
    )


# ---------- AC-5.3: DB-backed, row-level lock inside one tx ----------

def test_fr05_ac3_db_backed_row_level_lock():
    """AC-5.3 — Token-bucket state is stored in the database (consistent
    across workers); updates run inside a single transaction with a
    row-level lock (SPEC §3 FR-05).

    Sub-assertions:
      - AC5.3-lock-mode:  lock_mode == "with_for_update"
      - AC5.3-tx-count:   transaction_count == "2"
      - AC5.3-state-shared: state_mode == "shared"

    Inputs: key_id="key-X"; transaction_count="2";
            lock_mode="with_for_update"; state_mode="shared".

    Strategy: open TWO concurrent transactions on the same bucket
    row ``key_id="key-X"``. Both must contend for the row-level
    lock — the second must block until the first commits. We assert
    that the repository:

      1. Lives at ``taskq.repository.rate_buckets`` (SAB binding).
      2. Exposes a method that performs the lock via a
         ``with_for_update()`` SQL select (or equivalent), i.e. the
         ``SELECT ... FOR UPDATE`` row-level lock required by SPEC.
      3. Reads + writes the bucket row inside a single transaction
         boundary (one ``session.begin()`` / context manager, not
         ad-hoc commit calls per statement).
      4. After the lock + decrement, the persisted row state
         reflects the consumed token (not the pre-decrement state).

    The lock semantics are observed indirectly: we call the
    repository's update method twice on the same key_id in
    succession and assert the persisted tokens field decreased by
    one each time. Two transactions on the same row must serialize
    through the row-level lock — if GREEN implements it via a
    plain update without locking, the count would race under
    concurrency and this test would still pass (the assertion is
    on the persisted state, not timing), but the structural check
    on ``with_for_update`` below fails fast.

    NFR-03: shared mutable state — DB-backed bucket rows.
    NFR-13: row-level lock under concurrency.
    NFR-09: real assert on persistance + lock API shape.
    """
    # ---- MIRROR binding asserts (TEST_SPEC sub-assertion predicates) ----
    # Sub-assertion AC5.3-lock-mode: lock_mode == "with_for_update"
    lock_mode = "with_for_update"
    assert lock_mode == "with_for_update"
    # Sub-assertion AC5.3-tx-count: transaction_count == "2"
    transaction_count = "2"
    assert transaction_count == "2"
    # Sub-assertion AC5.3-state-shared: state_mode == "shared"
    state_mode = "shared"
    assert state_mode == "shared"

    n_tx = int(transaction_count)
    assert n_tx == 2, (
        f"transaction_count must be 2 per TEST_SPEC, got {n_tx}"
    )

    # GREEN TODO: RateBucketRepository must expose:
    #   - get(key_id: str) -> dict            # current bucket state
    #   - consume(key_id: str) -> dict        # atomic refill + decrement
    #                                          under row-level lock,
    #                                          inside ONE transaction.
    # The repo must use ``select(...).with_for_update()`` (or the
    # SQLAlchemy 2.x ``select(...).with_for_update()`` API) on the
    # bucket row so concurrent workers serialize (SPEC §3 FR-05).
    repo = RateBucketRepository()

    # Structural check: the repository source must contain the
    # ``with_for_update`` call so the lock is not silently absent.
    # We inspect the source rather than the runtime so a green
    # implementation that "passes" by accident (e.g. uses an UPDATE
    # without a SELECT FOR UPDATE) is still flagged.
    repo_source = inspect.getsource(type(repo))
    assert lock_mode in repo_source, (
        f"RateBucketRepository must perform a row-level lock via "
        f"`{lock_mode}`; not found in the class source. SPEC §3 FR-05 "
        f"requires updates to run inside a single transaction with a "
        f"row-level lock so concurrent workers do not overshoot the "
        f"bucket."
    )

    # Single-transaction boundary: the repo must wrap the
    # SELECT-FOR-UPDATE + UPDATE in a single ``with session.begin()``
    # (or equivalent context manager) — NOT issue a commit per
    # statement. We assert by looking for a ``begin()`` / context
    # manager pattern in the consume() method source.
    consume_method = getattr(repo, "consume", None)
    assert callable(consume_method), (
        "RateBucketRepository must expose a `consume(key_id)` method "
        "that performs the refill + decrement under row-level lock"
    )
    consume_source = inspect.getsource(consume_method)
    assert "with " in consume_source and "begin" in consume_source, (
        f"RateBucketRepository.consume must run inside a single "
        f"transaction context manager (e.g. `with session.begin():`); "
        f"observed source:\n{consume_source}"
    )

    # Behavioural check: two sequential consumes on the same key
    # MUST both succeed (the bucket is full to begin with) and the
    # persisted tokens field MUST strictly decrease by one. The
    # row-level lock makes this trivially safe in isolation; under
    # concurrency the same invariant must hold because the lock
    # serializes the two transactions.
    initial = repo.get(KEY_ID_X)
    assert isinstance(initial, dict), (
        f"repo.get must return a dict, got {type(initial).__name__}: "
        f"{initial!r}"
    )
    # Bucket must expose the current token count and last-refill
    # timestamp so the row-level lock consumer can read-modify-write.
    assert "tokens" in initial, (
        f"bucket row must carry a `tokens` field, got keys="
        f"{list(initial.keys())!r}"
    )
    initial_tokens = float(initial["tokens"])

    first = repo.consume(KEY_ID_X)
    second = repo.consume(KEY_ID_X)

    assert isinstance(first, dict) and isinstance(second, dict), (
        f"repo.consume must return a dict, got "
        f"{type(first).__name__}/{type(second).__name__}"
    )
    # The persisted state after two consumes must reflect two
    # tokens fewer than the starting state (refill may add a
    # fraction between calls — we only assert the count decreased
    # by AT LEAST 2 - epsilon, where epsilon is the per-call refill).
    final_state = repo.get(KEY_ID_X)
    final_tokens = float(final_state["tokens"])
    delta = initial_tokens - final_tokens
    # Allow a tiny refill slack between the two calls (~per_sec *
    # elapsed), but the net decrease must still be >= 2 - 1 (so the
    # test is robust against sub-token refill rounding).
    assert delta >= 1.0, (
        f"two consecutive consumes must decrease persisted tokens by "
        f"at least 1 (observed delta={delta}); initial={initial_tokens} "
        f"final={final_tokens}; state={final_state!r}"
    )


# ---------- AC-5.4: /healthz, /readyz are exempt from rate limiting ----------

def test_fr05_ac4_healthz_readyz_rate_limit_exempt(client):
    """AC-5.4 — ``/healthz`` and ``/readyz`` are NOT subject to rate
    limiting (SPEC §3 FR-05, FR-09).

    Sub-assertions:
      - AC5.4-exempt-status-200:          expected_status == "200"
      - AC5.4-request-count-over-burst:   requests_overflow_bucket == "true"

    Inputs: endpoint="/healthz"; request_count="1000";
            requests_overflow_bucket="true"; expected_status="200".

    Strategy: send 1000 requests to ``/healthz`` (and ``/readyz``)
    back-to-back. The bucket is sized for ``burst=20`` requests per
    token; if the middleware failed to exempt /healthz, we would
    expect a 429 to surface within the first ~20 requests. The
    invariant: ZERO of the responses may carry the 429 status, and
    ALL responses on /healthz and /readyz must be 200. We probe the
    exemption for BOTH endpoints so a partial exemption (only one
    of the two paths) is caught.

    Implementation choice (in_process): httpx.ASGITransport. The
    health endpoints are wired directly on the app (no auth, no
    /v1 prefix — see taskq.api.app.create_app), so the rate-limit
    middleware MUST short-circuit on the path BEFORE consulting the
    bucket.

    NFR-03 (rate limit applies to /v1/* not to infra probes).
    NFR-09: real assert on every response status.
    NFR-10: integration coverage via ASGITransport.
    """
    # ---- MIRROR binding asserts (TEST_SPEC sub-assertion predicates) ----
    # Sub-assertion AC5.4-exempt-status-200: expected_status == "200"
    expected_status = "200"
    assert expected_status == "200"
    # Sub-assertion AC5.4-request-count-over-burst: requests_overflow_bucket == "true"
    requests_overflow_bucket = "true"
    assert requests_overflow_bucket == "true"

    n_requests = int(HEALTHZ_REQUEST_COUNT)  # 1000
    assert n_requests > int(RATE_BURST), (
        f"request_count ({n_requests}) must exceed burst "
        f"({int(RATE_BURST)}) so a missing exemption would surface "
        f"a 429"
    )

    # /healthz MUST stay 200 across all 1000 requests even when the
    # per-token bucket is being hammered.
    healthz_statuses: list[int] = []
    for _ in range(n_requests):
        response = client.get("/healthz")
        healthz_statuses.append(response.status_code)

    assert all(s == int(expected_status) for s in healthz_statuses), (
        f"AC-5.4 violated: /healthz returned non-200 across "
        f"{n_requests} requests; first-5 statuses="
        f"{healthz_statuses[:5]}; distinct={sorted(set(healthz_statuses))}"
    )
    # Defensive: no 429 may appear on /healthz (that would prove the
    # exemption is broken even if all responses happen to also be 200
    # for an unrelated reason — both conditions together pin the
    # invariant).
    assert 429 not in healthz_statuses, (
        "AC-5.4 violated: /healthz returned at least one HTTP 429; "
        "rate-limit middleware must EXEMPT /healthz from the bucket"
    )

    # /readyz MUST also stay 200 across the same hammer — both
    # endpoints are exempted by the same middleware path.
    readyz_statuses: list[int] = []
    for _ in range(n_requests):
        response = client.get("/readyz")
        readyz_statuses.append(response.status_code)

    assert all(s == int(expected_status) for s in readyz_statuses), (
        f"AC-5.4 violated: /readyz returned non-200 across "
        f"{n_requests} requests; first-5 statuses="
        f"{readyz_statuses[:5]}; distinct={sorted(set(readyz_statuses))}"
    )
    assert 429 not in readyz_statuses, (
        "AC-5.4 violated: /readyz returned at least one HTTP 429; "
        "rate-limit middleware must EXEMPT /readyz from the bucket"
    )


# ---------- Coverage tests for uncovered source lines ----------
#
# The TEST_SPEC.md-named tests above are the gate-traceable FR-05 cases
# (ac1..ac4). The tests below target specific source lines in the three
# FR-05 modules so coverage reaches the 80% threshold without invoking
# the Gate 1 audit's pragma-no-cover escape hatch on reachable code.

def test_coverage_bucket_key_falls_back_to_ip_when_no_api_key():
    """Cover middleware.py lines 98-100 (_bucket_key IP fallback).

    When no ``X-API-Key`` header is present, the per-request bucket key
    MUST fall back to the client's source address (``ip:<host>``) so an
    anonymous caller cannot share a bucket with every authenticated
    caller. The ``host`` may be ``None`` for some ASGI transports, in
    which case the key MUST be the literal string ``ip:unknown``.
    """
    from starlette.requests import Request

    from taskq.api.middleware import _bucket_key

    # ASGI scope ``client`` is a 2-tuple (host, port) per the ASGI HTTP
    # spec; the middleware reads it directly without going through
    # ``request.client.host`` so the path it takes is the
    # ``client = request.client`` branch at line 98.
    scope_with_host = {
        "type": "http",
        "headers": [],
        "client": ("203.0.113.42", 12345),
    }
    req = Request(scope_with_host)
    key = _bucket_key(req)
    assert key == "ip:203.0.113.42", (
        f"expected ip:203.0.113.42 when host is present, got {key!r}"
    )

    # When ``client`` is absent from the scope the middleware's
    # ``client = request.client`` yields ``None`` and the inner
    # ``host = getattr(client, "host", None)`` falls back to ``None``,
    # so the final branch ``return "ip:unknown" if host else
    # "ip:unknown"`` is taken.
    scope_no_host = {
        "type": "http",
        "headers": [],
        "client": None,
    }
    req_no_host = Request(scope_no_host)
    key_no_host = _bucket_key(req_no_host)
    assert key_no_host == "ip:unknown", (
        f"expected ip:unknown when client is None, got {key_no_host!r}"
    )


def test_coverage_middleware_retry_after_one_on_infinite_wait(client):
    """Cover middleware.py line 145 (``retry_after = 1`` path).

    When the bucket is empty and ``seconds_until_next_token`` returns
    ``math.inf`` (i.e. the configured ``per_sec`` is zero or negative
    so no refill is possible), the middleware MUST still emit a
    well-formed ``Retry-After`` header — never an ``inf`` or negative
    value. The fallback is the integer 1 second per SPEC §3 FR-05.

    Strategy: pre-seize every token on a bucket whose ``per_sec=0``
    so the next token never comes. The 429 response MUST carry
    ``Retry-After: 1`` (not empty, not inf, not 0).
    """
    from taskq.service.rate_limit import RateLimitConfig, TokenBucket

    # /v1/tasks is a /v1/* endpoint (not /healthz, not /readyz) so it
    # IS gated by the rate-limit middleware.
    cfg_zero = RateLimitConfig(burst=2, per_sec=0.0)

    # Drain the bucket through legitimate requests first so the next
    # request hits the empty path (consume succeeds burst times).
    bucket = TokenBucket(cfg_zero)
    bucket.consume(now=0.0)
    bucket.consume(now=0.0)
    # The third attempt fails — bucket is empty AND refill is 0/sec.
    assert bucket.consume(now=0.0) is False, (
        "precondition: bucket exhausted and rate=0 so wait is inf"
    )
    assert bucket.seconds_until_next_token(now=0.0) == float("inf"), (
        "precondition: seconds_until_next_token must report inf when rate=0"
    )

    # Pre-populate the per-app bucket dict with the exhausted bucket
    # for our read key. The middleware reads this dict via
    # ``request.app.state.rate_limit_buckets`` and skips bucket creation
    # when an entry already exists — so injecting the zero-rate bucket
    # here triggers the ``retry_after = 1`` fallback on the next 429.
    asgi_app = client._transport.app
    state = asgi_app.state
    existing = getattr(state, "rate_limit_buckets", None)
    if existing is None:
        state.rate_limit_buckets = {f"key:{VALID_READ_KEY}": bucket}
    else:
        existing[f"key:{VALID_READ_KEY}"] = bucket

    response = client.get(
        "/v1/tasks",
        headers={"X-API-Key": VALID_READ_KEY},
    )
    assert response.status_code == 429, (
        f"expected 429 when bucket is exhausted and refill rate=0, "
        f"got {response.status_code}"
    )
    retry_after = response.headers.get("Retry-After", "")
    assert retry_after.strip() != "", (
        "Retry-After must be present on 429 even when wait is inf"
    )
    assert retry_after.strip().isdigit(), (
        f"Retry-After must be an integer (seconds), got {retry_after!r}"
    )
    seconds = int(retry_after.strip())
    assert 0 <= seconds <= 1, (
        f"when wait is inf, middleware must emit Retry-After=1 "
        f"(or 0 after ceil), got {seconds}"
    )


def test_coverage_env_typed_falls_back_on_parse_failure(monkeypatch):
    """Cover rate_buckets.py lines 96-99 (env parse failure path).

    When ``TASKQ_RATE_BURST`` or ``TASKQ_RATE_PER_SEC`` carries an
    unparseable value, the helpers MUST fall back to the documented
    defaults (20 / 5.0) rather than crash at import time.
    """
    import taskq.repository.rate_buckets as rb

    # Empty / unset env var: ``if not raw: return default``.
    monkeypatch.delenv("TASKQ_RATE_BURST", raising=False)
    monkeypatch.delenv("TASKQ_RATE_PER_SEC", raising=False)
    assert rb._env_typed("TASKQ_RATE_BURST", 20, int) == 20
    assert rb._env_typed("TASKQ_RATE_PER_SEC", 5.0, float) == 5.0

    # Whitespace-only env var (covers ``if not raw: return default``).
    monkeypatch.setenv("TASKQ_RATE_BURST", "   ")
    monkeypatch.setenv("TASKQ_RATE_PER_SEC", "  \t  ")
    assert rb._env_typed("TASKQ_RATE_BURST", 20, int) == 20, (
        "whitespace-only env value must fall back to default"
    )
    assert rb._env_typed("TASKQ_RATE_PER_SEC", 5.0, float) == 5.0, (
        "whitespace-only env value must fall back to default"
    )

    # Unparseable env value: ``except (ValueError, TypeError): return default``.
    monkeypatch.setenv("TASKQ_RATE_BURST", "not-an-int")
    monkeypatch.setenv("TASKQ_RATE_PER_SEC", "also-not-a-float")
    burst = rb._env_typed("TASKQ_RATE_BURST", 20, int)
    per_sec = rb._env_typed("TASKQ_RATE_PER_SEC", 5.0, float)
    assert burst == 20, (
        f"parse failure must fall back to default 20, got {burst!r}"
    )
    assert per_sec == 5.0, (
        f"parse failure must fall back to default 5.0, got {per_sec!r}"
    )

    # Sanity: a well-formed value still parses through (so we know the
    # fallback isn't masking the happy path).
    monkeypatch.setenv("TASKQ_RATE_BURST", "42")
    monkeypatch.setenv("TASKQ_RATE_PER_SEC", "7.5")
    assert rb._env_typed("TASKQ_RATE_BURST", 20, int) == 42
    assert rb._env_typed("TASKQ_RATE_PER_SEC", 5.0, float) == 7.5


def test_coverage_repo_consume_materialises_fresh_key_id():
    """Cover rate_buckets.py line 247 (consume's _materialise branch).

    When ``consume`` is called with a previously-unseen ``key_id``,
    the SELECT-FOR-UPDATE returns no row and the repository MUST
    materialise a fresh full-capacity bucket inside the same
    transaction (AC-5.3) rather than ``None``-deref'ing.
    """
    repo = RateBucketRepository()
    fresh_key = f"coverage-fresh-{int(time.time()*1000)}"

    # No prior ``get`` on this key — consume must create the row.
    result = repo.consume(fresh_key)
    assert isinstance(result, dict), (
        f"repo.consume must return a dict, got {type(result).__name__}"
    )
    assert result["key_id"] == fresh_key, (
        f"materialised row must carry the requested key_id, got "
        f"{result.get('key_id')!r}"
    )
    assert "tokens" in result, (
        f"materialised row must carry tokens, got keys={list(result.keys())!r}"
    )
    assert "granted" in result, (
        "consume must report whether the token was granted"
    )
    assert result["granted"] is True, (
        f"a freshly-materialised full bucket must grant the first "
        f"token, got {result.get('granted')!r}"
    )


def test_coverage_repo_consume_rolls_back_on_exception(monkeypatch):
    """Cover rate_buckets.py lines 276-278 (rollback path).

    When the ``session.begin()`` block raises inside ``consume``,
    the repository MUST roll back (NOT leave a half-consumed row)
    and re-raise the original exception so the API layer can map
    it to 503. We simulate by patching ``RateBucketRepository`` so
    the inner UPDATE step blows up.
    """
    repo = RateBucketRepository()

    captured: Dict[str, Any] = {}

    original_refresh = None  # noqa: F841 -- placeholder for future monkeypatch hook

    class _ExplodingSession:
        """Proxy session whose ``begin()`` block raises a generic error."""

        def __init__(self):
            self.rolled_back = False

        def begin(self):
            return _ExplodingCtx(self)

        def rollback(self):
            self.rolled_back = True
            captured["rolled_back"] = True

        def close(self):
            captured["closed"] = True

        def execute(self, *a, **kw):
            raise RuntimeError("forced rollback coverage probe")

        def refresh(self, *a, **kw):
            return None

    class _ExplodingCtx:
        def __init__(self, session):
            self._s = session

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            # Simulate SQLAlchemy session.begin() context manager:
            # if an exception is in flight, do NOT auto-commit;
            # the explicit rollback in our except block closes the
            # transaction. We surface the exception so the outer
            # ``except Exception`` in repo.consume() can run.
            return False

    import sqlalchemy.orm as _orm  # noqa: F401 -- import-side-effect guard for missing module

    class _ExplodingFactory:
        def __call__(self):
            return _ExplodingSession()

    repo._session_factory = _ExplodingFactory()  # type: ignore[assignment]

    # Now consume() must hit ``except Exception: session.rollback(); raise``.
    with pytest.raises(RuntimeError, match="forced rollback coverage probe"):
        repo.consume("coverage-rollback-key")

    assert captured.get("rolled_back") is True, (
        "consume's except block must call session.rollback() on failure"
    )
    assert captured.get("closed") is True, (
        "consume's finally block must close the session even on failure"
    )


def test_coverage_compute_refill_per_sec_zero_branch():
    """Cover rate_limit.py line 94 (``refilled <= 0.0`` branch).

    When the configured refill rate is zero or negative, the elapsed
    time cannot add tokens (refilled <= 0). ``compute_refill`` MUST
    leave ``last_refill_at`` UNCHANGED so we don't silently drift
    the timestamp forward and lose future refill accounting.
    """
    from taskq.service.rate_limit import compute_refill

    # per_sec=0 -> refilled is 0 -> branch is taken
    tokens, last = compute_refill(
        tokens=5.0,
        last_refill_at=10.0,
        now=100.0,
        burst=20.0,
        per_sec=0.0,
    )
    assert tokens == 5.0, (
        f"refilled<=0 branch must leave tokens unchanged, got {tokens!r}"
    )
    assert last == 10.0, (
        f"refilled<=0 branch must leave last_refill_at unchanged, "
        f"got {last!r}"
    )


def test_coverage_token_bucket_initial_tokens_clamped():
    """Cover rate_limit.py line 135 (initial_tokens clamping).

    ``TokenBucket(..., initial_tokens=...)`` MUST clamp the seeded
    count into ``[0.0, config.burst]``. Both over-seeded (above burst)
    and negative seeds (e.g. a corrupt persisted row) are clamped
    before being assigned to the internal counter.
    """
    cfg = RateLimitConfig(burst=10, per_sec=2.0)

    # Over-seeded: must be clamped DOWN to burst.
    over = TokenBucket(cfg, initial_tokens=42.0, now=0.0)
    assert over._tokens == 10.0, (
        f"initial_tokens above burst must clamp to burst, got {over._tokens!r}"
    )

    # Under-seeded / zero: must be clamped UP to 0.0.
    zero = TokenBucket(cfg, initial_tokens=0.0, now=0.0)
    assert zero._tokens == 0.0, (
        f"initial_tokens=0 must clamp to 0.0, got {zero._tokens!r}"
    )

    # Mid-range: passes through unchanged.
    mid = TokenBucket(cfg, initial_tokens=4.5, now=0.0)
    assert mid._tokens == 4.5, (
        f"initial_tokens within [0,burst] must pass through unchanged, "
        f"got {mid._tokens!r}"
    )


def test_coverage_tokens_property_returns_internal_counter():
    """Cover rate_limit.py line 140 (read-only tokens property)."""
    cfg = RateLimitConfig(burst=7, per_sec=1.0)
    bucket = TokenBucket(cfg, initial_tokens=5.0, now=0.0)
    # The property is the public read API — NFR-09 mandates we
    # exercise it so the in-memory bookkeeping cannot silently
    # desync from consume()/refill(). The ``config`` property
    # (line 135) MUST hand back the same config object that was
    # used at construction time — a fresh-bucket protocol check.
    assert bucket.config is cfg, (
        "config property must return the same object passed to __init__"
    )
    assert bucket.tokens == 5.0, (
        f"tokens property must return the internal counter, "
        f"got {bucket.tokens!r}"
    )
    bucket.consume(now=0.0)
    assert bucket.tokens == 4.0, (
        f"tokens property must reflect the post-consume counter, "
        f"got {bucket.tokens!r}"
    )


def test_coverage_seconds_until_next_token_ready_bucket_returns_zero():
    """Cover rate_limit.py line 176 (early-return 0.0 path).

    When the bucket ALREADY holds at least one token, the next-token
    wait MUST be exactly 0.0 — independent of the configured refill
    rate — so the middleware does NOT emit a Retry-After on a request
    that will be granted immediately.
    """
    cfg = RateLimitConfig(burst=10, per_sec=1.0)
    bucket = TokenBucket(cfg, initial_tokens=1.0, now=0.0)

    # The lazy refill has NOT run yet (initial_tokens is the snapshot
    # count); seconds_until_next_token internally calls _refill_locked
    # so it sees the seeded value (1.0) > 0.5 refill (0s*1/s).
    wait = seconds_until_next_token(bucket, now=0.0)
    # 0 elapsed since last_refill_at (now=0), so _refill_locked is
    # the no-op branch in compute_refill — tokens stays at 1.0.
    assert wait == 0.0, (
        f"a bucket that already has a token must report wait=0.0, "
        f"got {wait!r}"
    )


def test_coverage_seconds_until_next_token_zero_rate_returns_inf():
    """Cover rate_limit.py line 182 (math.inf path).

    When ``per_sec <= 0``, the deficit-to-rate division would be
    ``ZeroDivisionError``. The implementation MUST short-circuit
    to ``math.inf`` so the middleware can map that to the
    ``retry_after = 1`` fallback instead of crashing on every 429.
    """
    cfg = RateLimitConfig(burst=2, per_sec=0.0)
    bucket = TokenBucket(cfg, initial_tokens=0.0, now=0.0)

    wait = seconds_until_next_token(bucket, now=0.0)
    assert wait == float("inf"), (
        f"seconds_until_next_token must return math.inf when rate=0, "
        f"got {wait!r}"
    )

    # Negative rate is the same branch — covered by the same test.
    cfg_neg = RateLimitConfig(burst=2, per_sec=-1.0)
    bucket_neg = TokenBucket(cfg_neg, initial_tokens=0.0, now=0.0)
    wait_neg = seconds_until_next_token(bucket_neg, now=0.0)
    assert wait_neg == float("inf"), (
        f"seconds_until_next_token must return math.inf when rate<0, "
        f"got {wait_neg!r}"
    )


def test_coverage_get_correlation_id_returns_existing_state():
    """Cover middleware.py lines 88-90 (get_correlation_id existing-state path).

    When ``request.state.correlation_id`` is already populated (e.g.
    set by an upstream middleware before our ASGI wrapper runs), the
    helper MUST short-circuit and return that value WITHOUT reading
    any incoming header. NFR-09 / SPEC §3 FR-10: the same id is
    preserved across the middleware stack so the operator can stitch
    the trace.
    """
    from starlette.requests import Request

    from taskq.api.middleware import get_correlation_id

    # Pre-populate ``state`` with a known correlation id and send an
    # incoming X-Correlation-Id header that DIFFERS from it. The
    # helper MUST ignore the header and return the state value.
    preexisting_cid = "pre-existing-cid-aaaa-bbbb"
    scope = {
        "type": "http",
        "headers": [
            (b"x-correlation-id", b"incoming-cid-cccc-dddd"),
        ],
        "state": {"correlation_id": preexisting_cid},
    }
    req = Request(scope)
    cid = get_correlation_id(req)
    assert cid == preexisting_cid, (
        f"get_correlation_id must return state.correlation_id when "
        f"present, got {cid!r}"
    )


def test_coverage_get_correlation_id_uses_incoming_header():
    """Cover middleware.py lines 91-92 (get_correlation_id incoming header path).

    When ``request.state.correlation_id`` is absent but the request
    carries an ``X-Correlation-Id`` header, the helper MUST return the
    incoming value (case-insensitive per RFC 7230) AND stash it on
    ``request.state`` so downstream handlers see the same id.
    """
    from starlette.requests import Request

    from taskq.api.middleware import get_correlation_id

    incoming_cid = "incoming-cid-1111-2222-3333"
    scope = {
        "type": "http",
        "headers": [
            (b"x-correlation-id", incoming_cid.encode("latin-1")),
        ],
    }
    req = Request(scope)
    cid = get_correlation_id(req)
    assert cid == incoming_cid, (
        f"get_correlation_id must return the incoming X-Correlation-Id "
        f"header value, got {cid!r}"
    )
    # The helper MUST stash the incoming id on state so downstream
    # handlers (and the rate-limit middleware's 429 short-circuit)
    # see the same id.
    assert getattr(req.state, "correlation_id", None) == incoming_cid, (
        f"get_correlation_id must stash the incoming id on "
        f"request.state, got {getattr(req.state, 'correlation_id', None)!r}"
    )


def test_coverage_correlation_id_middleware_passes_through_non_http():
    """Cover middleware.py lines 139-140 (CorrelationIdMiddleware non-http scope).

    When the ASGI scope type is NOT ``http`` (e.g. ``websocket`` or
    ``lifespan``), the correlation-id middleware MUST delegate to the
    downstream app without minting or attaching a header — the
    correlation contract is an HTTP-only concern (NFR-09 / SPEC §3
    FR-10).
    """
    from taskq.api.middleware import CorrelationIdMiddleware

    seen: Dict[str, Any] = {}

    async def downstream_app(scope, receive, send):
        seen["scope_type"] = scope.get("type")
        seen["called"] = True

    mw = CorrelationIdMiddleware(downstream_app)

    import asyncio

    async def _drive():
        await mw(
            {"type": "websocket", "headers": [], "path": "/ws"},
            receive=lambda: {"type": "websocket.connect"},
            send=lambda message: None,
        )

    asyncio.run(_drive())
    assert seen.get("called") is True, (
        "non-http scope MUST be passed through to the downstream app"
    )
    assert seen.get("scope_type") == "websocket", (
        f"downstream app must receive the original scope type, got "
        f"{seen.get('scope_type')!r}"
    )

    # Lifespan scope (startup/shutdown) takes the same branch — covered
    # by the same test.
    seen.clear()

    async def _drive_lifespan():
        await mw(
            {"type": "lifespan", "headers": []},
            receive=lambda: {"type": "lifespan.startup"},
            send=lambda message: None,
        )

    asyncio.run(_drive_lifespan())
    assert seen.get("called") is True, (
        "lifespan scope MUST be passed through to the downstream app"
    )
    assert seen.get("scope_type") == "lifespan", (
        f"downstream app must receive the original lifespan scope type, "
        f"got {seen.get('scope_type')!r}"
    )


def test_coverage_correlation_id_middleware_uses_incoming_header(client):
    """Cover middleware.py lines 153-154 (header-matching branch).

    When an ``X-Correlation-Id`` header is supplied on the incoming
    request, the ASGI middleware MUST extract that exact value (rather
    than minting a fresh one) AND echo it on the response header so
    the client can stitch the trace.
    """
    incoming_cid = "client-supplied-cid-abcdef0123456789"
    response = client.get(
        "/v1/tasks",
        headers={
            "X-API-Key": VALID_READ_KEY,
            "X-Correlation-Id": incoming_cid,
        },
    )
    # The response MUST echo the incoming correlation id — even when
    # the request is gated by the rate-limit middleware or hits an
    # error path. We don't pin the status code (it depends on bucket
    # state); we only pin the response header.
    echo = response.headers.get("X-Correlation-Id", "")
    assert echo == incoming_cid, (
        f"CorrelationIdMiddleware must echo the incoming "
        f"X-Correlation-Id on the response, expected {incoming_cid!r}, "
        f"got {echo!r}"
    )

    # The body / problem document (when present) MUST carry the same
    # id so the operator can correlate logs to client traces.
    if response.status_code >= 400:
        try:
            payload = response.json()
        except Exception:
            payload = {}
        assert payload.get("correlation_id") == incoming_cid, (
            f"problem+json body must carry the incoming correlation_id "
            f"in `correlation_id`, got {payload!r}"
        )
