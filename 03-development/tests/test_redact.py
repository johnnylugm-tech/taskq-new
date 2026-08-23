"""Bug-hunt regression tests for NFR-04 subprocess output redaction.

These tests lock in the contract that ``taskq.security.redact.redact_text``
scrubs secret-shaped substrings (sk-… tokens, Bearer … JWTs,
postgres:// DSNs, password=… pairs) before subprocess output reaches
``task_results.stdout_tail`` / ``stderr_tail``. Reproduces the bug
hunt finding ``service.runner#2`` (no redaction in the runner output
path) and the SAD §6 T-10 declared mitigation that was previously
unimplemented.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

_THIS_DIR = Path(__file__).resolve().parent
_SRC_DIR = _THIS_DIR / "src"
if str(_SRC_DIR) not in sys.path:
    sys.path.insert(0, str(_SRC_DIR))

from taskq.security.redact import redact_text, _REDACTION_MARKER  # noqa: E402
from taskq.service.runner import TaskRunner  # noqa: E402


# ---------- Direct unit tests for redact_text ----------


def test_redact_text_scrubs_openai_style_key():
    """An ``sk-…`` token must be replaced by the redaction marker."""
    secret = "sk-abcdef1234567890XYZ"
    out = redact_text(f"deploy token={secret} expires=never")
    assert secret not in out, f"redact_text leaked the secret: {out!r}"
    assert _REDACTION_MARKER in out


def test_redact_text_scrubs_bearer_jwt():
    """A ``Bearer …`` JWT must be replaced."""
    secret = "Bearer eyJhbGciOiJIUzI1NiJ9.payload.signature"
    out = redact_text(f"Authorization: {secret}")
    assert secret not in out
    assert _REDACTION_MARKER in out


def test_redact_text_scrubs_postgres_dsn():
    """A postgres:// DSN must be replaced."""
    secret = "postgres://user:hunter2@db.local:5432/app"
    out = redact_text(f"connecting to {secret} now")
    assert "hunter2" not in out, f"password leaked through redaction: {out!r}"
    assert _REDACTION_MARKER in out


def test_redact_text_scrubs_password_kv():
    """A ``password=…`` key/value pair must be replaced."""
    out = redact_text("password=hunter2 db=prod")
    assert "hunter2" not in out
    assert _REDACTION_MARKER in out


def test_redact_text_preserves_clean_text():
    """Redaction MUST NOT modify ordinary log lines that carry no secret."""
    safe = "build ok: 12 tests passed in 0.4s\nall green"
    assert redact_text(safe) == safe


def test_redact_text_empty_returns_empty():
    assert redact_text("") == ""
    assert redact_text(None) == ""  # type: ignore[arg-type]


# ---------- Integration: TaskRunner does not leak secrets ----------


def test_taskrunner_does_not_leak_sk_token_in_stdout_tail(monkeypatch):
    """Reproduces bug-hunt finding ``service.runner#2``.

    The runner's ``stdout_tail`` MUST NOT carry a plaintext sk-… token
    even if the subprocess emitted one. Prior to the fix this test
    failed (RED) — the runner wrote the subprocess bytes verbatim.
    """
    import asyncio
    import os
    import sys
    from taskq.service import runner as runner_mod

    secret = "sk-prod-leak-9999abcdef"

    class _FakeProc:
        returncode = 0

        async def communicate(self):
            return (f"hello\nkey={secret}\n".encode("utf-8"), b"")

    async def _fake_spawn(argv):
        return _FakeProc()

    # Make sure the spawn path goes to the fake; bypass shlex side-effects.
    monkeypatch.setattr(TaskRunner, "_spawn", staticmethod(_fake_spawn))

    runner = TaskRunner(timeout=5.0)
    result = asyncio.run(runner._execute(task_id="t1", command="echo hello"))

    assert result["stdout_tail"], "runner must capture stdout"
    assert secret not in result["stdout_tail"], (
        f"TaskRunner persisted a secret-shaped token verbatim: "
        f"{result['stdout_tail']!r}"
    )
    assert _REDACTION_MARKER in result["stdout_tail"], (
        f"expected redaction marker in stdout_tail; got {result['stdout_tail']!r}"
    )


# ---------- Optional: ensure redact_text handles multiple patterns in one line ----------


def test_redact_text_scrubs_multiple_patterns_on_one_line():
    """Multiple secret-shaped substrings on one line must all be scrubbed."""
    line = (
        "header: Bearer eyJabc.def.ghi payload sk-prod12345 "
        "password=topsecret postgres://u:p@h/db"
    )
    out = redact_text(line)
    for forbidden in ("eyJabc.def.ghi", "sk-prod12345", "topsecret", "u:p@h"):
        assert forbidden not in out, f"redact leaked {forbidden!r}: {out!r}"
    # The redaction marker must appear at least three times.
    assert out.count(_REDACTION_MARKER) >= 3


def test_redact_module_wired_into_service_runner():
    """Sanity: the service.runner module imports the redact helper.

    This test fails if a future refactor removes the import — a RED
    signal that stdout/stderr is no longer being scrubbed before
    persistence.
    """
    import taskq.service.runner as runner_mod

    src = Path(runner_mod.__file__).read_text(encoding="utf-8")
    assert "redact_text" in src, (
        "taskq.service.runner no longer imports redact_text; "
        "stdout/stderr may be persisted without NFR-04 scrubbing."
    )