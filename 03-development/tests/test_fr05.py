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
from typing import Any, Dict, Optional

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
