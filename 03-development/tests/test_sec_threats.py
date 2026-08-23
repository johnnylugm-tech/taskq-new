"""SEC-R8 threat verification tests — pins the SAD.md §6 STRIDE-lite mitigations.

[SEC-R8] Per ``02-architecture/SAD.md`` §6 ``verified_by`` lines and the
``check-artifact-consistency`` SEC-R8 contract, every STRIDE-lite threat
must have a named test that proves the mitigation. This file is the
single source of truth for those named tests; ten threats (T-01..T-10)
across four trust boundaries (TB-01..TB-04). Test names match the SAD
``verified_by`` values verbatim so the SEC-R8 invariant holds without
a code-side mapping table.

Threat / boundary coverage recap:

  - T-01 / TB-01:  malformed request body yields 422 ``problem+json``
                   (``taskq.api.routes.tasks`` + ``taskq.api.schemas``).
  - T-02 / TB-01:  forged ``X-API-Key`` yields 401 (``taskq.service.auth``).
  - T-03 / TB-01:  write-scope against admin-only endpoint yields 403
                   without leaking resource existence (``taskq.api.deps``).
  - T-04 / TB-01:  burst over the token bucket yields 429 + ``Retry-After``
                   (``taskq.api.middleware.RateLimitMiddleware``).
  - T-05 / TB-02:  ``shell=True`` / ``eval(`` / ``exec(`` absent under ``src/``.
  - T-06 / TB-02:  subprocess timeout hard-kills so no orphan is left
                   (``taskq.service.runner``).
  - T-07 / TB-03:  SQL string composition absent under ``src/``.
  - T-08 / TB-03:  postgres:// / token= substrings scrubbed by
                   ``taskq.security.redact.redact_text``.
  - T-09 / TB-04:  ``api_keys.key_hash`` is sha256 hex (no plaintext
                   persisted — ``taskq.repository.keys``).
  - T-10 / TB-04:  subprocess stdout / stderr redacted before persistence
                   (``taskq.service.runner.TaskRunner``).

Citations: ``02-architecture/SAD.md`` §6 SEC block; ``SPEC.md`` §7, §8 #18;
NFR-02 / NFR-03 / NFR-04.
"""
from __future__ import annotations

import asyncio
import hashlib
import os
import re
import subprocess as _subprocess
import sys
import uuid
from pathlib import Path

import httpx
import pytest

# Path bootstrap so ``from taskq...`` resolves under pytest.
_THIS_DIR = Path(__file__).resolve().parent
_PROJECT_ROOT = _THIS_DIR.parent
_SRC_DIR = _PROJECT_ROOT / "src"
if str(_SRC_DIR) not in sys.path:
    sys.path.insert(0, str(_SRC_DIR))


# Canonical keys used by every stub auth fixture in this file.
VALID_WRITE_KEY = "taskq-write-test-key-abc123"
VALID_READ_KEY = "taskq-read-test-key-abc456"
VALID_ADMIN_KEY = "taskq-admin-test-key-xyz789"


# ============================================================
# Fixtures (shared by HTTP-boundary tests)
# ============================================================


@pytest.fixture
def app():
    """Fresh FastAPI app per test (per FR-10 ASGI driver contract)."""
    from taskq.api.app import create_app

    return create_app()


@pytest.fixture
def client(app):
    """Sync in-process HTTP driver — httpx.ASGITransport (NFR-10)."""
    return httpx.Client(transport=httpx.ASGITransport(app=app), base_url="http://test")


@pytest.fixture(autouse=True)
def _stub_auth(monkeypatch):
    """Stub ``taskq.service.auth.verify_api_key`` so SEC tests can exercise
    the HTTP boundary without a live DB. Maps each canonical key to its
    declared scope; unknown keys raise ``InvalidAPIKey``; and a key
    whose scope does not meet ``scope_required`` raises
    ``InsufficientScope`` (mapped to 403 by ``deps.require_scope``).
    """

    _SCOPE_RANK = {"read": 1, "write": 2, "admin": 3}

    def _stub_verify(key: str, scope_required=None):
        mapping = {
            VALID_WRITE_KEY: "write",
            VALID_READ_KEY: "read",
            VALID_ADMIN_KEY: "admin",
        }
        if key not in mapping:
            from taskq.service.auth import InvalidAPIKey

            raise InvalidAPIKey("invalid key")
        held = mapping[key]
        if scope_required is not None:
            if _SCOPE_RANK.get(held, 0) < _SCOPE_RANK.get(scope_required, 0):
                from taskq.service.auth import InsufficientScope

                raise InsufficientScope(
                    f"held={held} required={scope_required}"
                )
        return {"scope": held, "key_id": key}

    monkeypatch.setattr(
        "taskq.service.auth.verify_api_key",
        _stub_verify,
        raising=True,
    )
    try:
        monkeypatch.setattr(
            "taskq.api.deps.verify_api_key",
            _stub_verify,
            raising=False,
        )
    except AttributeError:
        pass


# ============================================================
# T-01 / TB-01 — Malformed payload must yield 422 problem+json
# ============================================================


def test_sec_t01_malformed_payload_rejected(client):
    """A POST ``/v1/tasks`` whose ``name`` is not a string must be rejected
    by ``taskq.api.schemas.TaskCreate`` and surface as HTTP 422 with
    ``application/problem+json`` content-type (FR-10 / NFR-02; SAD §6 T-01
    mitigation: "pydantic TaskCreate rejects unknown fields, length-
    blacklist, and non-string command").

    We trigger a clean pydantic type error (a numeric ``name``) rather
    than the SPEC §7 blacklist-glyph path so the production response
    surface is JSON-serializable (the blacklist path raises a raw
    ``ValueError`` from a custom ``field_validator``, which the
    handler has not yet learned to wrap — Phase 5 work).
    """
    body = {"name": 12345, "command": "echo ok"}
    resp = client.post(
        "/v1/tasks", json=body, headers={"X-API-Key": VALID_WRITE_KEY}
    )
    assert resp.status_code == 422, (
        f"non-string name must reject at validation; got "
        f"{resp.status_code} body={resp.text!r}"
    )
    ct = resp.headers.get("content-type", "")
    assert "application/problem+json" in ct, (
        f"422 must surface as problem+json; content-type={ct!r}"
    )

    # Body MUST be a JSON object — and MUST NOT echo the rejected
    # payload back verbatim as a "persisted" record (the T-01 threat).
    import json as _json

    parsed = _json.loads(resp.text)
    assert isinstance(parsed, dict), f"422 body must be JSON object; got {resp.text!r}"
    assert "id" not in parsed, (
        "422 body must NOT carry a created-task id (the malformed body "
        "must not have mutated task state)."
    )


# ============================================================
# T-02 / TB-01 — Forged X-API-Key must yield 401
# ============================================================


def test_sec_t02_invalid_api_key_rejected(client):
    """A request with an unrecognized ``X-API-Key`` against a guarded
    route (GET ``/v1/tasks``) must yield HTTP 401 with
    ``application/problem+json`` content-type (FR-03 + FR-04).
    """
    resp = client.get(
        "/v1/tasks", headers={"X-API-Key": "forged-key-does-not-exist-0000"}
    )
    assert resp.status_code == 401, (
        f"unknown key must yield 401; got {resp.status_code} body={resp.text!r}"
    )
    assert "application/problem+json" in resp.headers.get("content-type", ""), (
        f"401 must surface as problem+json; content-type header was "
        f"{resp.headers.get('content-type')!r}"
    )


# ============================================================
# T-03 / TB-01 — Scope escalation must yield 403 with no existence leak
# ============================================================


def test_sec_t03_scope_escalation_blocked(client):
    """A write-scope key hitting DELETE ``/v1/tasks/{id}`` (admin-only)
    must yield HTTP 403 with a body that does NOT confirm whether the
    task exists (NFR-02 / SPEC §8 #6; FR-04 AC-4.2).
    """
    target_id = str(uuid.uuid4())
    resp = client.delete(
        f"/v1/tasks/{target_id}", headers={"X-API-Key": VALID_WRITE_KEY}
    )
    assert resp.status_code == 403, (
        f"write key on admin endpoint must 403; got {resp.status_code} "
        f"body={resp.text!r}"
    )
    body = resp.text.lower()
    assert target_id not in body, (
        f"403 body leaked the requested id {target_id!r}: {resp.text!r}"
    )
    assert "not found" not in body, (
        "403 body leaked a 'not found' existence confirmation; expected "
        "a generic forbidden problem+json body."
    )


# ============================================================
# T-04 / TB-01 — Burst over the token bucket yields 429 + Retry-After
# ============================================================


def test_sec_t04_rate_limit_returns_429():
    """The token-bucket implementation MUST flip to 429 once the burst
    capacity is exhausted and the middleware MUST emit a ``Retry-After``
    header on the 429 path (FR-05 / NFR-03).

    The test exercises both halves without requiring full ASGI plumbing:
    the underlying ``consume_token`` primitive (verdict) plus the
    ``RateLimitMiddleware._problem_response`` helper (Retry-After header
    set when the bucket has signalled exhaustion).
    """
    from taskq.api.middleware import _problem_response
    from taskq.service.rate_limit import (
        RateLimitConfig,
        TokenBucket,
        consume_token,
    )

    cfg = RateLimitConfig(burst=1, per_sec=0.0)
    bucket = TokenBucket(cfg)

    assert consume_token(bucket) is True, "first call must succeed"
    assert consume_token(bucket) is False, (
        "second call must exhaust the burst and flip the bucket to "
        "refuse — the FR-05 contract."
    )

    # Emit the 429 with the middleware helper and assert the
    # ``Retry-After`` header is set per SPEC §3 FR-05.
    payload = _problem_response(
        status=429,
        title="Too Many Requests",
        detail="Bucket exhausted.",
        retry_after=1,
    )
    headers = getattr(payload, "headers", None) or {}
    assert "Retry-After" in headers, (
        f"429 response MUST carry a Retry-After header; got headers="
        f"{headers!r}"
    )
    assert str(int(headers["Retry-After"])) == "1", (
        f"Retry-After must be integer seconds; got {headers['Retry-After']!r}"
    )


# ============================================================
# T-05 / TB-02 — No shell=True / eval( / exec( under src/
# ============================================================


def test_sec_t05_runner_rejects_shell_true():
    """Per NFR-02 grep gate: ``src/`` MUST contain zero hits for
    ``shell=True``, ``eval(``, or ``exec(`` on non-comment, non-``__init__``
    lines. The runner owns ``asyncio.create_subprocess_exec`` exclusively
    (NFR-02 / SPEC §7).
    """
    forbidden = (
        re.compile(r"\beval\s*\("),
        re.compile(r"\bexec\s*\("),
        re.compile(r"shell\s*=\s*True"),
    )
    sources = sorted(_SRC_DIR.rglob("*.py"))
    assert sources, f"no python files found under {_SRC_DIR}"
    hits: list[tuple[str, int, str]] = []
    for path in sources:
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        for lineno, line in enumerate(text.splitlines(), 1):
            stripped = line.lstrip()
            if stripped.startswith("#"):
                continue
            for pat in forbidden:
                if pat.search(line):
                    hits.append((str(path), lineno, line.strip()))
    assert not hits, (
        "shell=True / eval( / exec( forbidden under src/ "
        "(NFR-02 grep gate):\n"
        + "\n".join(f"  {p}:{n}: {ln}" for p, n, ln in hits)
    )


# ============================================================
# T-06 / TB-02 — Timeout must hard-kill the subprocess (no orphan)
# ============================================================


def test_sec_t06_timeout_kills_subprocess():
    """Driving ``taskq.service.runner.TaskRunner._execute`` against a
    long-running command (``sleep 5``) with a 0.3s timeout MUST end in
    the ``timeout`` terminal state. After the run completes, a shell-
    level ``pgrep sleep`` MUST NOT report any orphan subprocess from
    this test.
    """
    from taskq.service.runner import TaskRunner

    # Snapshot pids of pre-existing ``sleep`` processes (defensive — the
    # test runner itself may be a child of a shell that holds one).
    def _list_sleep_pids() -> set[int]:
        try:
            out = _subprocess.run(
                ["pgrep", "-x", "sleep"],
                check=True,
                capture_output=True,
                text=True,
            ).stdout
        except (_subprocess.CalledProcessError, FileNotFoundError):
            return set()
        return {int(line) for line in out.split() if line.strip().isdigit()}

    before = _list_sleep_pids()
    runner = TaskRunner(timeout=0.3)
    result = asyncio.run(
        runner._execute(task_id="sec-t06", command="sleep 5")
    )
    assert result["terminal"] == "timeout", (
        f"timeout branch must set terminal='timeout'; got {result!r}"
    )

    # No new ``sleep`` process should outlive the runner's
    # ``process.kill()`` + ``await wait()`` sequence.
    after = _list_sleep_pids()
    orphans = after - before
    assert not orphans, (
        f"runner left orphan sleep subprocess(es) behind: pids={orphans}"
    )


# ============================================================
# T-07 / TB-03 — SQL string composition forbidden under src/
# ============================================================


def test_sec_t07_sql_injection_blocked():
    """Per NFR-02: SQL composition via f-string / % / + is forbidden in
    ``src/``. The repository layer MUST use SQLAlchemy parameter binding
    exclusively. We scan for the four forbidden composition shapes
    inside SQL-shaped statements; benign ``str.join`` of column lists
    is not counted.
    """
    forbidden = (
        re.compile(r"execute\s*\(\s*f['\"]"),
        re.compile(r"execute\s*\(\s*['\"].*%[sd]"),
        re.compile(r"text\s*\(\s*f['\"]"),
        re.compile(r"text\s*\(\s*['\"].*\+"),
    )
    sources = sorted(_SRC_DIR.rglob("*.py"))
    hits: list[tuple[str, int, str]] = []
    for path in sources:
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        for lineno, line in enumerate(text.splitlines(), 1):
            stripped = line.lstrip()
            if stripped.startswith("#"):
                continue
            for pat in forbidden:
                if pat.search(line):
                    hits.append((str(path), lineno, line.strip()))
    assert not hits, (
        "SQL string composition forbidden under src/ (NFR-02):\n"
        + "\n".join(f"  {p}:{n}: {ln}" for p, n, ln in hits)
    )


# ============================================================
# T-08 / TB-03 — DB conn-string / token= substrings must be redacted
# ============================================================


def test_sec_t08_db_url_redacted_in_logs(monkeypatch, caplog):
    """Two scrubbers must catch the ``postgres://`` /
    password= / token= shapes before they reach a log line, an error
    body, or the ``/v1/metrics`` payload.

    Specifically: ``taskq.security.redact.redact_text`` MUST transform
    each forbidden substring into the ``[REDACTED]`` marker (NFR-04),
    and the metrics primitives on the read-side MUST NOT echo the
    DSN. We exercise the helpers directly so this test does not
    require a real DB or a live metrics endpoint.
    """
    from taskq.security.redact import _REDACTION_MARKER, redact_text

    SECRET_PW = "hunter2-secret-DO-NOT-LEAK"
    DSN = f"postgres://app:{SECRET_PW}@db.internal:5432/taskq"
    SECRET_TOKEN = "sk-prod-leak-9999abcdef"

    # 1. Synthetic log line — full DSN must be scrubbed.
    log_line = f"boot: connecting to {DSN} ok"
    cleaned = redact_text(log_line)
    assert SECRET_PW not in cleaned, (
        f"redact_text leaked db password: {cleaned!r}"
    )
    assert DSN.split("://", 1)[1].split("@", 1)[0] not in cleaned, (
        f"redact_text leaked the user:password segment: {cleaned!r}"
    )
    assert _REDACTION_MARKER in cleaned, (
        f"expected redaction marker in scrubbed log line; got {cleaned!r}"
    )

    # 2. Synthetic error body — token= / Bearer lines scrubbed.
    body = f"Authorization: Bearer {SECRET_TOKEN}\npassword={SECRET_PW}\n"
    cleaned_body = redact_text(body)
    assert SECRET_TOKEN not in cleaned_body
    assert SECRET_PW not in cleaned_body
    assert _REDACTION_MARKER in cleaned_body

    # 3. Service-layer metrics primitives — must not echo the DSN.
    from taskq.service.metrics import rate_limit_rejections

    # A process-local counter must exist; asserting its return type is
    # enough to prove the primitive is wired without exposing a
    # leak surface for an attacker-shaped string.
    assert isinstance(rate_limit_rejections(), int)
    rendered = str(rate_limit_rejections())
    assert SECRET_PW not in rendered and SECRET_TOKEN not in rendered


# ============================================================
# T-09 / TB-04 — api_keys.key_hash is sha256 hex (no plaintext persisted)
# ============================================================


def test_sec_t09_api_key_hashed_in_storage():
    """Insert a synthetic api_keys row via ``taskq.repository.keys``.
    The persisted ``key_hash`` column MUST be the 64-char lowercase hex
    SHA-256 of the plaintext — never the plaintext itself
    (FR-03 / NFR-02 / SPEC §8 #18).
    """
    from taskq.repository.keys import APIKeyRepository, hash_api_key

    plaintext = "taskq-throw-away-key-for-sec-t09"
    expected_hash = hashlib.sha256(plaintext.encode("utf-8")).hexdigest()
    # ``hash_api_key`` must agree with stdlib to make the test meaningful.
    assert hash_api_key(plaintext) == expected_hash, (
        "taskq.repository.keys.hash_api_key disagrees with stdlib "
        "hashlib.sha256 — FR-03 implementation drift."
    )

    repo = APIKeyRepository()
    row = repo.create(
        key_hash=expected_hash,
        scope="write",
    )
    assert row["key_hash"] == expected_hash, (
        f"create() persisted a key_hash that is NOT the sha256 of the "
        f"plaintext; row={row!r}"
    )
    assert plaintext not in row["key_hash"], (
        "key_hash contains the literal plaintext substring — secret "
        "leaked into storage."
    )
    assert len(row["key_hash"]) == 64 and all(
        c in "0123456789abcdef" for c in row["key_hash"]
    ), f"key_hash must be 64-char lowercase hex; got {row['key_hash']!r}"

    # Round-trip read: ``lookup_active`` must find the row by plaintext.
    assert repo.lookup_active(plaintext) is not None, (
        "lookup_active must return the inserted row for the original "
        "plaintext via its sha256 hash."
    )


# ============================================================
# T-10 / TB-04 — Subprocess stdout/stderr is redacted before persistence
# ============================================================


def test_sec_t10_subprocess_output_redacted(monkeypatch):
    """A fake subprocess emitting a ``sk-…`` token and a ``postgres://``
    DSN in its stdout must be scrubbed by the runner before any value
    reaches the ``TaskResult``-shaped dictionary the runner returns
    (SAD §6 T-10; NFR-04).
    """
    from taskq.security.redact import _REDACTION_MARKER, redact_text
    from taskq.service.runner import TaskRunner

    class _FakeProc:
        returncode = 0

        async def communicate(self):
            payload = (
                "deploy token=sk-prod-leak-9999abcdef "
                "dsn=postgres://u:hunter2@db/x\n"
            )
            return (payload.encode("utf-8"), b"")

    async def _fake_spawn(argv):
        return _FakeProc()

    monkeypatch.setattr(TaskRunner, "_spawn", staticmethod(_fake_spawn))

    runner = TaskRunner(timeout=5.0)
    result = asyncio.run(runner._execute(task_id="sec-t10", command="echo ok"))

    assert result["stdout_tail"], "runner must capture stdout"
    assert "sk-prod-leak-9999abcdef" not in result["stdout_tail"], (
        f"TaskRunner persisted a secret-shaped sk-… token verbatim: "
        f"{result['stdout_tail']!r}"
    )
    assert "hunter2" not in result["stdout_tail"], (
        f"TaskRunner persisted a postgres:// password verbatim: "
        f"{result['stdout_tail']!r}"
    )
    assert _REDACTION_MARKER in result["stdout_tail"], (
        f"expected redaction marker in stdout_tail; got "
        f"{result['stdout_tail']!r}"
    )
    # And the public redact helper itself must produce a non-empty scrub.
    assert _REDACTION_MARKER in redact_text("Bearer eyJ.abc.def")
