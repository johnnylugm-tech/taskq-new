"""TaskRunner — FR-02 subprocess executor.

[FR-02] Synchronous façade that internally drives an asyncio event loop
to use ``asyncio.create_subprocess_exec(*shlex.split(command))`` with a
hard-kill timeout. Returns a dict carrying:

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

Citations: SPEC.md §3 FR-02, §8 #16; SAD.md §4 service/runner.
"""
from __future__ import annotations

import asyncio
import os
import shlex
import time
from datetime import datetime, timezone
from typing import Any, Dict, Optional


DEFAULT_TIMEOUT_SECONDS: float = 30.0
TAIL_LIMIT: int = 8000  # matches stdout_tail / stderr_tail column width

# Sentinel exit code emitted on the timeout path. Distinct from any
# real POSIX exit code (0..255) so the repository can recognise it.
TIMEOUT_EXIT_CODE: int = -1


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _decode(stream: Optional[bytes]) -> str:
    """Decode captured bytes to a tail-capped UTF-8 string."""
    if not stream:
        return ""
    text = stream.decode(errors="replace")
    if len(text) > TAIL_LIMIT:
        text = text[-TAIL_LIMIT:]
    return text


class TaskRunner:
    """Subprocess executor for FR-02.

    Stateless — each ``run()`` call drives a private event loop so the
    sync façade works from both the in-process tests (driver threads)
    and FastAPI's BackgroundTasks (threadpool). The async private path
    is where ``asyncio.create_subprocess_exec`` + ``wait_for`` live.
    """

    def __init__(self, timeout: Optional[float] = None) -> None:
        self._timeout = (
            timeout if timeout is not None else self._read_timeout()
        )

    # ---- env / timeout ----

    @staticmethod
    def _read_timeout() -> float:
        """Read ``TASKQ_TASK_TIMEOUT`` from env, falling back to the default."""
        raw = os.environ.get("TASKQ_TASK_TIMEOUT")
        if raw is None:
            return DEFAULT_TIMEOUT_SECONDS
        try:
            value = float(raw)
        except ValueError:
            return DEFAULT_TIMEOUT_SECONDS
        return value if value > 0 else DEFAULT_TIMEOUT_SECONDS

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
            await self._hard_kill(proc)
            return self._build_result(
                task_id=task_id,
                command=command,
                terminal="timeout",
                exit_code=TIMEOUT_EXIT_CODE,
                stdout_tail="",
                stderr_tail="",
                started=started_monotonic,
            )

        exit_code = proc.returncode
        if exit_code is None:
            # communicate() completed without raising, so the subprocess
            # has exited; any remaining None is an internal contract
            # violation that must not be persisted as a sentinel exit code.
            raise RuntimeError(
                "subprocess returncode is None after communicate() completed"
            )
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
    async def _hard_kill(proc: asyncio.subprocess.Process) -> None:
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
            pass

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


__all__ = ["TaskRunner", "DEFAULT_TIMEOUT_SECONDS", "TIMEOUT_EXIT_CODE"]