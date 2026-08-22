"""Tests for FR-03: API Key authentication (SHA-256 + hmac.compare_digest).

Test names MUST match TEST_SPEC.md (`02-architecture/TEST_SPEC.md`)
section "FR-03: API Key authentication" for the canonical six cases
(test_fr03_ac1..ac6). spec-coverage-check uses exact match.

NFR-09 (zero-skip / no xfail): every test in this file performs real asserts
on the FR-03 modules / HTTP boundary. No skip / xfail / assertion-free stubs
are permitted (AC-N9.1..AC-N9.7).

SAB module declarations for FR-03 (binding on the GREEN implementation —
Gate 1's Architecture Amendment Protocol blocks phantom modules):

  - taskq.cli.key_create        -> AC-3.4 plaintext print-once
  - taskq.repository.keys       -> AC-3.2 sha256 hash, AC-3.3 hmac.compare_digest,
                                   AC-3.5 revoked_at -> invalid
  - taskq.models.api_key        -> AC-3.2 api_keys row schema, AC-3.5 revoked_at
  - taskq.service.auth          -> AC-3.1, AC-3.5 (replaces FR-01 dict-stub with
                                   sha256-backed verification against api_keys)

Citations: SPEC.md §3 FR-03, §7, §8 #5, #18; SAD.md §4 auth/repository/models;
NFR-02 (constant-time compare; no plaintext on the wire / in logs / metrics);
NFR-04 (no plaintext in logs / error body / metrics).
"""
from __future__ import annotations

import contextlib
import hashlib
import hmac
import io
import re
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

import httpx
import pytest

# ---- Import path bootstrap ----
# Tests must reach the modules declared by SAB.json for FR-03. We add the
# src root to sys.path so the dotted names below resolve once GREEN lands.
_THIS_DIR = Path(__file__).resolve().parent
_SRC_DIR = _THIS_DIR / "src"
if str(_SRC_DIR) not in sys.path:
    sys.path.insert(0, str(_SRC_DIR))

# ---- Standard top-level imports (NO try/except ImportError) ----
# A missing module below is the EXPECTED RED state: pytest will surface
# ModuleNotFoundError as a Collection Error, which is the validated
# failure signal for this step (FR-03 implementation has not landed yet).

# GREEN TODO: taskq.cli.key_create must expose ``main(argv: list[str]) -> int``
# that creates an API key and prints the plaintext to stdout exactly once.
from taskq.cli.key_create import main as key_create_main  # noqa: E402

# GREEN TODO: taskq.repository.keys must expose:
#   - hash_api_key(plaintext: str) -> str   # 64-char lowercase hex sha256
#   - verify_api_key(candidate: str, stored_hash: str) -> bool  # uses hmac.compare_digest
#   - APIKeyRepository class with create() / lookup_active() / etc.
from taskq.repository.keys import (  # noqa: E402
    APIKeyRepository,
    hash_api_key,
    verify_api_key,
)

# GREEN TODO: taskq.models.api_key must declare the ORM model with at least:
#   id (str/uuid), key_hash (String(64)), scope (str),
#   created_at (datetime), revoked_at (nullable datetime).
from taskq.models.api_key import APIKey  # noqa: E402

# GREEN TODO: taskq.service.auth.verify_api_key must be backed by the
# api_keys table (sha256 hash + hmac.compare_digest) and reject keys
# with non-null revoked_at. The FR-01 dict-stub must be removed.
from taskq.service.auth import verify_api_key as service_verify  # noqa: E402

# GREEN TODO: taskq.api.app.create_app must additionally register
# /healthz and /readyz endpoints under the root prefix (no auth required,
# per FR-09). These are exercised by AC-3.6.
from taskq.api.app import create_app  # noqa: E402


# ---------- Constants declared by TEST_SPEC Inputs rows ----------

SAMPLE_KEY = "taskq-test-key-abc123"            # AC-3.2
CANDIDATE_KEY = "candidate-key"                 # AC-3.3
STORED_HASH_64 = "deadbeef" + "0" * 56          # AC-3.3 (64 hex chars)
REVOKED_KEY = "revoked-key"                     # AC-3.5
REVOKED_AT = datetime(2026, 8, 1, 0, 0, 0, tzinfo=timezone.utc)  # AC-3.5


# ---------- Fixtures ----------

@pytest.fixture
def app():
    """Fresh FastAPI app per test (function-scoped)."""
    return create_app()


@pytest.fixture
def transport(app):
    """In-process HTTP driver via httpx.ASGITransport."""
    return httpx.ASGITransport(app=app)


@pytest.fixture
def client(transport):
    """Sync client; in_process mode (decision: in_process)."""
    return httpx.Client(transport=transport, base_url="http://test")


@pytest.fixture(autouse=True)
def _isolate_external_state(monkeypatch):
    """Per-test isolation.

    The HTTP-level tests (AC-3.1, AC-3.5) rely on a clean api_keys table;
    the conftest's _reset_taskq_db autouse fixture only resets the tasks
    schema. For FR-03 GREEN the api_keys table must be reset too — but for
    this RED step we keep the fixture intentionally minimal so the test
    fails because the FR-03 module is missing, not because of side-effects.
    """
    yield


# ---------- AC-3.1: missing/invalid X-API-Key -> 401 + problem+json ----------

def test_fr03_ac1_missing_or_invalid_key_returns_401(client):
    """AC-3.1 — missing/invalid X-API-Key on /v1/* -> 401 + application/problem+json.

    Sub-assertions:
      - AC3.1-status-401:       expected_status == "401"
      - AC3.1-content-type:     expected_content_type == "application/problem+json"

    Inputs: api_key="invalid-key" (no api_keys row will hash to this value).

    Implementation choice (in_process): drive via httpx.ASGITransport so
    the route's FastAPI dependency chain (Header -> _require_scope ->
    taskq.service.auth.verify_api_key) executes under test.

    NFR-02 (no stack / SQL / path leak): the 401 body is the RFC 7807
    problem document, not a stack trace.
    NFR-10 (integration coverage via ASGITransport): end-to-end through
    the FastAPI app boundary.
    # NFR-02
    # NFR-09
    # NFR-10
    """
    # Sub-assertion AC3.1-status-401: 401 (no api_keys row matches).
    response = client.get(
        "/v1/tasks",
        headers={"X-API-Key": "invalid-key"},
    )
    assert response.status_code == 401, response.text

    # Sub-assertion AC3.1-content-type: RFC 7807 problem+json media type.
    ctype = response.headers.get("content-type", "")
    assert ctype.startswith("application/problem+json"), (
        f"expected application/problem+json, got {ctype!r}; "
        f"body={response.text!r}"
    )


# ---------- AC-3.2: api_keys.key_hash is 64-char hex sha256; no plaintext ----------

def test_fr03_ac2_key_hash_sha256_64hex_no_plaintext():
    """AC-3.2 — api_keys.key_hash is exactly 64 lowercase hex chars (sha256);
    the table holds no plaintext keys.

    Sub-assertions:
      - AC3.2-hash-len-64:        expected_hash_len == "64"
      - AC3.2-algo-sha256:        expected_algo == "sha256"
      - AC3.2-hash-charset-hex:   hash_charset == "hex"

    Inputs: sample_key = "taskq-test-key-abc123".

    GREEN TODO: taskq.repository.keys.hash_api_key(plaintext: str) -> str
    must call hashlib.sha256(plaintext.encode("utf-8")).hexdigest().
    # NFR-02
    # NFR-04
    # NFR-09
    """
    sample_key = SAMPLE_KEY  # "taskq-test-key-abc123"

    # GREEN TODO: hash_api_key must return sha256(plaintext) as lowercase hex.
    hashed: str = hash_api_key(sample_key)

    # Sub-assertion AC3.2-hash-len-64: exactly 64 characters.
    assert len(hashed) == 64, (
        f"expected 64-char hex hash, got length {len(hashed)}: {hashed!r}"
    )
    # Sub-assertion AC3.2-algo-sha256: matches hashlib.sha256(plaintext).hexdigest()
    expected_sha256 = hashlib.sha256(sample_key.encode("utf-8")).hexdigest()
    assert hashed == expected_sha256, (
        f"hash is not sha256(sample): got {hashed!r}, want {expected_sha256!r}"
    )
    # Sub-assertion AC3.2-hash-charset-hex: lowercase hex only.
    assert re.fullmatch(r"[0-9a-f]{64}", hashed), (
        f"hash is not lowercase hex [0-9a-f]{{64}}: {hashed!r}"
    )
    # NFR-02 / SPEC §8 #18: no plaintext leak in the stored hash.
    assert sample_key not in hashed, (
        "plaintext substring leaked into the hash value"
    )
    # Independent char-set independence: no whitespace, no uppercase.
    assert hashed == hashed.lower(), "hash contains uppercase characters"
    assert " " not in hashed and "\n" not in hashed, "hash contains whitespace"


# ---------- AC-3.3: verify_api_key uses hmac.compare_digest (constant-time) ----------

def test_fr03_ac3_compare_digest_constant_time(monkeypatch):
    """AC-3.3 — verify_api_key uses hmac.compare_digest (constant-time compare).

    Sub-assertions:
      - AC3.3-compare-api:  compare_api == "hmac.compare_digest"
      - AC3.3-module-hmac:  expected_module == "hmac"

    Inputs: api_key="candidate-key"; stored_hash="deadbeef" + "0"*56.

    Implementation choice: spy on hmac.compare_digest via monkeypatch.
    We do NOT replace the function — we wrap it so real comparison still
    happens — but we record the call so the test can assert GREEN routed
    the comparison through hmac.compare_digest (NOT ``==``).
    # NFR-02
    # NFR-09
    """
    # Spy that records arguments and forwards to the real compare_digest.
    captured: Dict[str, Any] = {}
    real_compare_digest = hmac.compare_digest

    def _spy(a: Any, b: Any) -> bool:
        captured["called"] = True
        captured["a"] = a
        captured["b"] = b
        return real_compare_digest(a, b)

    monkeypatch.setattr(hmac, "compare_digest", _spy)

    # Inputs from TEST_SPEC: api_key="candidate-key"; stored_hash="deadbeef" + "0"*56
    candidate = CANDIDATE_KEY
    stored_hash = STORED_HASH_64  # "deadbeef" + "0" * 56 (64 hex chars)
    assert len(stored_hash) == 64, (
        f"stored_hash must be 64 hex chars per TEST_SPEC, got {len(stored_hash)}"
    )

    # GREEN TODO: taskq.repository.keys.verify_api_key(candidate, stored_hash)
    # must compare via hmac.compare_digest (not ``==``). Returns bool.
    result = verify_api_key(candidate=candidate, stored_hash=stored_hash)

    # Sub-assertion AC3.3-module-hmac: hmac.compare_digest WAS the comparison API.
    assert captured.get("called") is True, (
        "verify_api_key did NOT call hmac.compare_digest — "
        "constant-time comparison contract violated (NFR-02 / AC-3.3)."
    )

    # The candidate key must reach compare_digest on one of the operands.
    # GREEN may either (a) pass the raw plaintext candidate + raw stored_hash,
    # or (b) hash the candidate first then pass sha256(candidate) + stored_hash.
    a_arg = captured.get("a")
    b_arg = captured.get("b")
    candidate_hash = hash_api_key(candidate)
    plausible_pairs = [
        (candidate, stored_hash),
        (stored_hash, candidate),
        (candidate_hash, stored_hash),
        (stored_hash, candidate_hash),
        (candidate, candidate_hash),
        (candidate_hash, candidate),
    ]
    assert (a_arg, b_arg) in plausible_pairs, (
        f"compare_digest called with unexpected args: a={a_arg!r}, b={b_arg!r}; "
        f"expected one of {plausible_pairs!r}"
    )

    # Sanity: return type is a bool (spy still returns the real result).
    assert isinstance(result, bool), (
        f"verify_api_key must return bool, got {type(result).__name__}"
    )


# ---------- AC-3.4: key creation prints plaintext ONCE; no persistence of plaintext ----------

def test_fr03_ac4_create_prints_plaintext_once(monkeypatch):
    """AC-3.4 — key creation prints the plaintext to stdout exactly once and
    never persists it.

    Sub-assertions:
      - AC3.4-stdout-once:    expected_stdout_lines == "1"
      - AC3.4-no-persist:     plaintext_persistence == "forbidden"

    Inputs: scope="write".

    Implementation choice (in_process, per integration_fr_guidelines):
    capture stdout via contextlib.redirect_stdout + io.StringIO. monkeypatch
    APIKeyRepository.create so we can observe exactly what the CLI persists
    WITHOUT touching a real DB.
    # NFR-04
    # NFR-05
    # NFR-09
    """
    # Capture every payload the CLI hands to the repository. The plaintext
    # must NEVER appear in any of these dicts (AC-3.4 / NFR-04).
    persisted_payloads: List[Dict[str, Any]] = []

    def _fake_create(self, **kwargs: Any) -> Dict[str, Any]:
        persisted_payloads.append(dict(kwargs))
        return {"id": "fake-key-id", **kwargs}

    # GREEN TODO: APIKeyRepository.create must accept scope + key_hash
    # (NEVER plaintext). The CLI must compute hash_api_key(plaintext)
    # locally and pass only the hash to the repository.
    monkeypatch.setattr(APIKeyRepository, "create", _fake_create)

    # Invoke the CLI in-process (pytest-cov cannot measure subprocess
    # coverage — we must exercise the handler in-process for GATE1).
    buf_out = io.StringIO()
    buf_err = io.StringIO()
    # GREEN TODO: argv per SPEC `python -m taskq.cli.key_create key create
    # --scope <scope>` — the in-process call strips the script name, so
    # main() receives sys.argv[1:] = ["key", "create", "--scope", "write"].
    argv = ["key", "create", "--scope", "write"]
    with contextlib.redirect_stdout(buf_out), contextlib.redirect_stderr(buf_err):
        exit_code = key_create_main(argv)

    assert exit_code == 0, (
        f"key_create_main exited {exit_code}; stderr={buf_err.getvalue()!r}"
    )

    stdout_text = buf_out.getvalue()
    # Sub-assertion AC3.4-stdout-once: exactly one non-empty line on stdout.
    lines = [ln for ln in stdout_text.splitlines() if ln.strip()]
    assert len(lines) == 1, (
        f"expected exactly 1 stdout line (plaintext printed once), "
        f"got {len(lines)}: {lines!r}"
    )
    plaintext_line = lines[0]
    # The plaintext must be a non-trivial secret token (>= 16 chars).
    assert len(plaintext_line) >= 16, (
        f"plaintext line too short to be a real key: {plaintext_line!r}"
    )

    # Sub-assertion AC3.4-no-persist: plaintext must NEVER reach the repo.
    assert len(persisted_payloads) == 1, (
        f"expected exactly 1 repository.create() call, got "
        f"{len(persisted_payloads)}: {persisted_payloads!r}"
    )
    persisted = persisted_payloads[0]
    for field_name, field_value in persisted.items():
        assert field_value != plaintext_line, (
            f"plaintext leaked into APIKeyRepository.create("
            f"{field_name}={field_value!r})"
        )
    # The repository payload must include the SHA-256 hash, not the key.
    assert "key_hash" in persisted, (
        f"key_hash missing from repository.create payload: {persisted!r}"
    )
    assert len(persisted["key_hash"]) == 64, (
        f"persisted key_hash is not 64 hex chars: {persisted['key_hash']!r}"
    )
    assert persisted["key_hash"] == hash_api_key(plaintext_line), (
        "persisted key_hash does not match sha256(plaintext); "
        "the CLI must hash before persisting"
    )


# ---------- AC-3.5: revoked_at non-null -> key invalid (HTTP 401) ----------

def test_fr03_ac5_revoked_key_invalid(client, monkeypatch):
    """AC-3.5 — a key with non-null revoked_at is treated as invalid (HTTP 401).

    Sub-assertion:
      - AC3.5-revoked-status: expected_status == "401"

    Inputs: api_key="revoked-key"; revoked_at_iso="2026-08-01T00:00:00Z".

    Setup: pre-insert an api_keys row whose key_hash matches "revoked-key"
    AND whose revoked_at is non-null. Then call /v1/* with that key and
    expect 401 (problem+json envelope, same as AC-3.1).

    GREEN TODO: APIKey must accept a revoked_at datetime.
    GREEN TODO: APIKeyRepository must expose an insert/upsert that writes
    a row including revoked_at.
    GREEN TODO: taskq.service.auth.verify_api_key must look up by
    sha256(candidate), find the row, observe revoked_at is non-null,
    and raise InvalidAPIKey (mapped to 401 + problem+json).
    # NFR-02
    # NFR-04
    # NFR-09
    """
    revoked_plaintext = REVOKED_KEY  # "revoked-key"
    revoked_hash = hash_api_key(revoked_plaintext)

    revoked_row = APIKey(
        id=str(uuid.uuid4()),
        key_hash=revoked_hash,
        scope="read",
        revoked_at=REVOKED_AT,  # 2026-08-01T00:00:00Z
    )

    # Insert into the api_keys table (in-memory SQLite via the FR-03 repo).
    # GREEN TODO: APIKeyRepository must expose create() that takes an APIKey
    # (or equivalent fields) and persists to the api_keys table.
    APIKeyRepository().create(model=revoked_row)

    # The HTTP request uses the REVOKED plaintext — the service layer must
    # hash it, find the revoked row, and return 401.
    response = client.get(
        "/v1/tasks",
        headers={"X-API-Key": revoked_plaintext},
    )

    # Sub-assertion AC3.5-revoked-status: revoked key -> 401.
    assert response.status_code == 401, (
        f"expected 401 for revoked key, got {response.status_code}: {response.text!r}"
    )


# ---------- AC-3.6: /healthz and /readyz do not require authentication ----------

def test_fr03_ac6_healthz_readyz_no_auth(client):
    """AC-3.6 — /healthz and /readyz do not require authentication (HTTP 200
    with no X-API-Key header).

    Sub-assertion:
      - AC3.6-no-auth-healthz: expected_status == "200"

    Inputs: endpoint="/healthz"; api_key="" (no X-API-Key header at all).

    Implementation choice (in_process): httpx.ASGITransport, no auth
    header. GREEN TODO: taskq.api.app.create_app must mount /healthz
    and /readyz under the root prefix with NO auth dependency.
    # NFR-09
    # NFR-10
    """
    # /healthz with NO X-API-Key header.
    response = client.get("/healthz")
    # Sub-assertion AC3.6-no-auth-healthz: 200, not 401.
    assert response.status_code == 200, (
        f"/healthz returned {response.status_code} (expected 200, no auth). "
        f"body={response.text!r}"
    )

    # /readyz must likewise succeed without auth (SPEC §3 FR-09).
    response_ready = client.get("/readyz")
    assert response_ready.status_code == 200, (
        f"/readyz returned {response_ready.status_code} (expected 200, no auth). "
        f"body={response_ready.text!r}"
    )
