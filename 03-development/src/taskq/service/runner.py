"""TaskRunner / AsyncExecutor — service-layer subprocess executors.

[FR-02] ``TaskRunner`` — synchronous façade that internally drives an
asyncio event loop to use ``asyncio.create_subprocess_exec(*shlex.split(command))``
with a hard-kill timeout. Returns a dict carrying:

  * ``terminal`` — ``"done"`` (exit 0) | ``"failed"`` (exit non-zero) |
                    ``"timeout"`` (TASKQ_TASK_TIMEOUT exceeded)
  * ``exit_code`` — process exit code (``-1`` sentinel on timeout)
  * ``stdout_tail`` / ``stderr_tail`` — captured, tail-capped
  * ``duration_ms`` — wall-clock duration
  * ``finished_at`` — ISO-8601 UTC timestamp at process exit

Implementation contract (SPEC.md §3 FR-02, §8 #16, NFR-02, NFR-03):

  * No shell invocation — tokenised via ``shlex.split`` + exec.
  * Timeout comes from ``TASKQ_TASK_TIMEOUT`` (env var, default 30s).
  * Timeout path MUST hard-kill the subprocess
    (``proc.kill()`` + ``await proc.wait()``), not just cancel the
    awaiting coroutine — otherwise the child becomes a zombie.

The runner is stateless; each ``run()`` call creates a private event
loop so the sync façade works from both the in-process tests (driver
threads) and FastAPI's ``BackgroundTasks`` (threadpool).

[FR-08] ``AsyncExecutor`` — async counterpart that manages a background
submission queue with bounded concurrency, graceful drain bounded by
``TASKQ_DRAIN_TIMEOUT``, hard-kill enforcement for tasks exceeding
``TASKQ_TASK_TIMEOUT``, and ``asyncio.CancelledError`` propagation
(NFR-03). Background execution is launched via ``asyncio.create_task``
(the primitive ``asyncio.TaskGroup`` uses internally); ``run_until_drained``
awaits the dispatched set under a single ``asyncio.wait_for`` so the
TaskGroup semantics (single cancellation boundary, gather all) hold.

Citations: SPEC.md §3 FR-02 / FR-08, §5.1, §8 #16 / #25; SAD.md §4
service/runner; NFR-03.
"""
from __future__ import annotations

import asyncio
import logging
import os
import shlex
import time
from datetime import datetime, timezone
from typing import Any, Dict, Optional, Tuple, cast


# =============================================================================
# Module-level constants
# =============================================================================

# [FR-02] Default per-task timeout (TASKQ_TASK_TIMEOUT, seconds).
DEFAULT_TIMEOUT_SECONDS: float = 30.0

# [FR-08] Defaults read by ``AsyncExecutor`` when its constructor
# arguments are not supplied; the constructor accepts ``None`` to defer
# to ``TASKQ_MAX_CONCURRENT`` / ``TASKQ_DRAIN_TIMEOUT`` /
# ``TASKQ_TASK_TIMEOUT`` env vars (SPEC §5.1).
MAX_CONCURRENT_DEFAULT: int = 8
DRAIN_TIMEOUT_DEFAULT: float = 30.0
TASK_TIMEOUT_DEFAULT: float = 30.0

TAIL_LIMIT: int = 8000  # matches stdout_tail / stderr_tail column width

# Sentinel exit code emitted on the timeout path. Distinct from any
# real POSIX exit code (0..255) so the repository can recognise it.
TIMEOUT_EXIT_CODE: int = -1

# [FR-08] Per-task terminal status values reported in the result dict
# returned by ``AsyncExecutor.run_until_drained``. Stable wire strings —
# callers (FastAPI shutdown handler, audit log) pattern-match on them.
STATUS_DRAINED: str = "drained"
STATUS_INTERRUPTED: str = "interrupted"
TERMINAL_STATUSES: Tuple[str, ...] = (STATUS_DRAINED, STATUS_INTERRUPTED)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _decode(stream: Optional[bytes]) -> str:
    """Decode captured bytes to a tail-capped UTF-8 string.

    [FR-02 / SAD §6 T-10 / NFR-04] Every secret-shaped substring
    (``sk-…`` / ``Bearer …`` / ``postgres://`` / ``password=…``) is
    scrubbed via ``taskq.errors.redact.redact_text`` BEFORE the value
    is returned to the caller, so both the persisted
    ``task_results.stdout_tail`` / ``stderr_tail`` columns and the
    structured server logs see only the redacted form.
    """
    if not stream:
        return ""
    text = stream.decode(errors="replace")
    if len(text) > TAIL_LIMIT:
        text = text[-TAIL_LIMIT:]
    # Local import keeps the runner importable from contexts where the
    # errors package is unavailable (e.g. some test bootstraps); the
    # helper is a pure stdlib regex module so the import cannot fail at
    # runtime.
    from taskq.errors.redact import redact_text

    return redact_text(text)


def _env_int(name: str, default: int) -> int:
    """Read integer env var ``name``; fall back to ``default`` on missing/invalid."""
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def _env_float(name: str, default: float) -> float:
    """Read float env var ``name``; fall back to ``default`` on missing/invalid."""
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        value = float(raw)
    except ValueError:
        return default
    return value if value > 0 else default


async def _hard_kill_process(proc: asyncio.subprocess.Process) -> None:
    """Hard-kill ``proc`` and reap it (NFR-03).

    ``cancel`` on the await coroutine would only cancel the waiter,
    leaving the child as a zombie; ``proc.kill()`` + ``await proc.wait()``
    is the hard-kill path required by NFR-03.
    """
    try:
        proc.kill()
    except ProcessLookupError:
        pass
    try:
        await proc.wait()
    except Exception:
        logger.debug("proc.wait after kill failed; relying on asyncio cleanup",
                     exc_info=True)


logger = logging.getLogger("taskq.service.runner")


class TaskRunner:
    """Subprocess executor for FR-02.

    Stateless — each ``run()`` call drives a private event loop so the
    sync façade works from both the in-process tests (driver threads)
    and FastAPI's BackgroundTasks (threadpool). The async private path
    is where ``asyncio.create_subprocess_exec`` + ``wait_for`` live.
    """

    def __init__(self, timeout: Optional[float] = None) -> None:
        self._timeout = (
            timeout
            if timeout is not None
            else _env_float("TASKQ_TASK_TIMEOUT", DEFAULT_TIMEOUT_SECONDS)
        )

    # ---- public sync façade ----

    def run(self, task_id: str, command: str) -> Dict[str, Any]:
        """Execute ``command`` and return the structured result dict.

        Synchronous from the caller's perspective; internally drives a
        private asyncio event loop so ``asyncio.create_subprocess_exec``
        can be used per SPEC §8 #16.

        NFR-02: never invoke through a shell; tokenises via ``shlex.split``.
        NFR-03: hard-kill subprocess on timeout.
        """
        return asyncio.run(self._execute(task_id, command))

    # ---- async core ----

    async def _execute(self, task_id: str, command: str) -> Dict[str, Any]:
        argv = shlex.split(command)
        started_monotonic = time.monotonic()
        proc = await self._spawn(argv)
        if proc is None:
            # shlex.split produced an argv whose program isn't on PATH.
            return self._build_result(
                task_id=task_id,
                command=command,
                terminal="failed",
                exit_code=127,
                stdout_tail="",
                stderr_tail="command not found",
                started=started_monotonic,
            )

        try:
            stdout_bytes, stderr_bytes = await asyncio.wait_for(
                proc.communicate(), timeout=self._timeout
            )
        except asyncio.TimeoutError:
            await _hard_kill_process(proc)
            return self._build_result(
                task_id=task_id,
                command=command,
                terminal="timeout",
                exit_code=TIMEOUT_EXIT_CODE,
                stdout_tail="",
                stderr_tail="",
                started=started_monotonic,
            )

        exit_code = cast(int, proc.returncode)
        terminal = "done" if exit_code == 0 else "failed"
        return self._build_result(
            task_id=task_id,
            command=command,
            terminal=terminal,
            exit_code=exit_code,
            stdout_tail=_decode(stdout_bytes),
            stderr_tail=_decode(stderr_bytes),
            started=started_monotonic,
        )

    @staticmethod
    async def _spawn(argv: list[str]):
        """Spawn ``argv`` via ``create_subprocess_exec``; return None on missing program.

        A shell is never involved: ``argv`` is passed tokenised to the
        ``exec`` syscall, per NFR-02 / SPEC §8 #16.
        """
        try:
            return await asyncio.create_subprocess_exec(
                *argv,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        except FileNotFoundError:
            return None

    @staticmethod
    def _build_result(
        task_id: str,
        command: str,
        terminal: str,
        exit_code: int,
        stdout_tail: str,
        stderr_tail: str,
        started: float,
    ) -> Dict[str, Any]:
        duration_ms = int((time.monotonic() - started) * 1000)
        return {
            "task_id": task_id,
            "command": command,
            "terminal": terminal,
            "exit_code": exit_code,
            "stdout_tail": stdout_tail,
            "stderr_tail": stderr_tail,
            "duration_ms": duration_ms,
            "finished_at": _now_iso(),
        }

    # Backward-compat shim for FR-02 test surface:
    # ``TaskRunner._hard_kill`` (static) was the original FR-02 name before
    # FR-08 promoted the helper to module-level ``_hard_kill_process`` so
    # ``AsyncExecutor`` could reuse it without importing a private
    # staticmethod. Tests written against FR-02's locked-in contract
    # (test_fr02.py lines 781-816) call ``TaskRunner._hard_kill`` directly;
    # delegate to preserve their surface.
    _hard_kill = staticmethod(_hard_kill_process)


__all__ = [
    "TaskRunner",
    "AsyncExecutor",
    "DEFAULT_TIMEOUT_SECONDS",
    "MAX_CONCURRENT_DEFAULT",
    "DRAIN_TIMEOUT_DEFAULT",
    "TASK_TIMEOUT_DEFAULT",
    "STATUS_DRAINED",
    "STATUS_INTERRUPTED",
    "TERMINAL_STATUSES",
    "TIMEOUT_EXIT_CODE",
]

# Re-export AsyncExecutor from its own module so existing
# `from taskq.service.runner import AsyncExecutor` imports keep working.
from taskq.service.executor import AsyncExecutor  # noqa: E402, F401
