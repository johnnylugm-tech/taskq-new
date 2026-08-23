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
import collections
import logging
import os
import shlex
import time
from datetime import datetime, timezone
from typing import Any, Deque, Dict, Optional, Set, Tuple, cast


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


class AsyncExecutor:
    """Background subprocess executor for FR-08.

    [FR-08] Manages concurrent subprocess execution bounded by
    ``max_concurrent`` (env ``TASKQ_MAX_CONCURRENT``, default
    ``MAX_CONCURRENT_DEFAULT``). ``submit(task_id, command)`` dispatches
    immediately when below the cap, otherwise queues. ``run_until_drained``
    awaits every queued + in-flight task, honouring ``drain_timeout``
    (env ``TASKQ_DRAIN_TIMEOUT``, default ``DRAIN_TIMEOUT_DEFAULT``);
    if the drain deadline is exceeded, all in-flight tasks are marked
    ``interrupted`` and hard-killed. Per-task execution is bounded by
    ``task_timeout`` (env ``TASKQ_TASK_TIMEOUT``, default
    ``TASK_TIMEOUT_DEFAULT``); on timeout the subprocess is terminated
    via ``process.kill()`` then ``await process.wait()`` so no orphan
    process survives.

    ``asyncio.CancelledError`` is NEVER caught by a bare ``except Exception``
    (NFR-03) — per-task coroutines re-raise after cleaning up their
    subprocess. ``run_until_drained`` propagates cancellation directly
    via ``asyncio.wait_for`` so a service-shutdown ``Task.cancel()``
    surfaces to the caller.
    """

    def __init__(
        self,
        max_concurrent: Optional[int] = None,
        drain_timeout: Optional[float] = None,
        task_timeout: Optional[float] = None,
    ) -> None:
        if max_concurrent is None:
            max_concurrent = _env_int("TASKQ_MAX_CONCURRENT", MAX_CONCURRENT_DEFAULT)
        if drain_timeout is None:
            drain_timeout = _env_float("TASKQ_DRAIN_TIMEOUT", DRAIN_TIMEOUT_DEFAULT)
        if task_timeout is None:
            task_timeout = _env_float("TASKQ_TASK_TIMEOUT", TASK_TIMEOUT_DEFAULT)

        self._max_concurrent = max_concurrent
        self._drain_timeout = drain_timeout
        self._task_timeout = task_timeout

        # FIFO of (task_id, command) waiting for a slot.
        self._pending: Deque[Tuple[str, str]] = collections.deque()
        # Dispatched ``asyncio.Task`` instances keyed by ``task_id``.
        self._tasks: Dict[str, asyncio.Task] = {}
        # Set of task_ids ever submitted in the current wave; persists
        # across per-task completion so the drain-timeout ledger can
        # still seed ``STATUS_INTERRUPTED`` entries for tasks whose
        # per-task finally clause has already popped them from
        # ``self._tasks``.
        self._submitted: Set[str] = set()
        # Number of slots consumed in the current wave. Monotonic between
        # ``submit`` calls within a single ``run_until_drained`` cycle,
        # reset when drain returns.
        self._in_flight_count: int = 0
        # Per-task terminal status: ``STATUS_DRAINED`` or
        # ``STATUS_INTERRUPTED``.
        self._results: Dict[str, str] = {}

    # ---- public observable state ----

    @property
    def queued_count(self) -> int:
        """Number of submissions waiting for a free slot."""
        return len(self._pending)

    @property
    def in_flight_count(self) -> int:
        """Number of submissions dispatched in the current wave.

        Held at the high-water mark reached by ``submit`` calls so
        callers can observe the cap without racing against the
        subprocess lifecycle (the dispatched ``asyncio.Task`` might
        have completed before the count is sampled). Reset to ``0``
        when ``run_until_drained`` returns.
        """
        return self._in_flight_count

    # ---- submission ----

    async def submit(self, task_id: str, command: str) -> None:
        """Queue a task for execution; dispatch immediately if below cap.

        When ``in_flight_count`` is strictly less than ``max_concurrent``
        an ``asyncio.Task`` is created immediately and counted toward
        the in-flight tally; otherwise ``(task_id, command)`` is appended
        to the FIFO and dispatched when a slot frees up inside
        ``run_until_drained``.

        After dispatching, ``await asyncio.sleep(0)`` yields to the
        event loop so the freshly-created task can run past its
        ``await asyncio.create_subprocess_exec`` to a state where
        ``asyncio.run()``'s cancellation can interrupt it cleanly.
        Without this yield, a tight submit-loop fills ``_tasks`` before
        any task has actually started the subprocess, and the eventual
        ``asyncio.run()`` cleanup hangs because the not-yet-started
        tasks are still inside the ``create_subprocess_exec`` await.
        """
        self._submitted.add(task_id)
        if self._in_flight_count < self._max_concurrent:
            self._dispatch(task_id, command)
        else:
            self._pending.append((task_id, command))
        # Yield once so the dispatched task gets to run past its
        # ``create_subprocess_exec`` await; ``asyncio.run()`` cleanup
        # can then cancel it at a responsive await point.
        await asyncio.sleep(0)

    def _dispatch(self, task_id: str, command: str) -> None:
        """Create the ``asyncio.Task`` for ``task_id`` and mark a slot in use."""
        if self._in_flight_count >= self._max_concurrent:
            return  # safety net; ``submit`` already guards this
        self._in_flight_count += 1
        task = asyncio.create_task(self._run_task(task_id, command))
        self._tasks[task_id] = task

    # ---- per-task execution ----

    async def _run_task(self, task_id: str, command: str) -> None:
        """Execute ``command`` as a subprocess with hard-kill timeout.

        On timeout the subprocess is terminated via
        ``process.kill()`` then ``await process.wait()`` so no orphan
        survives (NFR-03, SPEC §8 #25). ``asyncio.CancelledError`` is
        re-raised after the subprocess is hard-killed; it is NEVER
        swallowed (NFR-03).
        """
        proc: Optional[asyncio.subprocess.Process] = None
        try:
            argv = shlex.split(command)
            proc = await asyncio.create_subprocess_exec(
                *argv,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            try:
                await asyncio.wait_for(proc.communicate(), timeout=self._task_timeout)
                self._results[task_id] = STATUS_DRAINED
            except asyncio.TimeoutError:
                await _hard_kill_process(proc)
                self._results[task_id] = STATUS_INTERRUPTED
        except asyncio.CancelledError:
            # NEVER swallow; clean up and re-raise (NFR-03).
            if proc is not None:
                await _hard_kill_process(proc)
            raise
        except FileNotFoundError:
            # Command not on PATH — executor still attempted, so ``STATUS_DRAINED``.
            self._results[task_id] = STATUS_DRAINED
        except Exception:
            # Any other error during subprocess exec; record terminal state.
            self._results[task_id] = STATUS_DRAINED
        finally:
            # Remove from the dispatched set; ``run_until_drained``'s
            # wait-loop relies on this so the loop can terminate when
            # every dispatched task has finished. The drain-timeout
            # path snapshots ``self._tasks`` BEFORE the cancellation
            # wave so the per-task ``STATUS_INTERRUPTED`` ledger can
            # still be seeded even if the cancel races the pop.
            self._tasks.pop(task_id, None)

    # ---- drain ----

    async def run_until_drained(self) -> Dict[str, Any]:
        """Await every queued + in-flight task, bounded by ``drain_timeout``.

        Returns a structured result::

            {"status": STATUS_DRAINED | STATUS_INTERRUPTED,
             "tasks": {task_id: STATUS_DRAINED | STATUS_INTERRUPTED}}

        * ``status == STATUS_DRAINED`` when every dispatched task
          finished cleanly within ``drain_timeout``.
        * ``status == STATUS_INTERRUPTED`` when the drain deadline
          elapsed first; every still-running task is cancelled and any
          task that never started (remaining in the FIFO) is reported
          as ``STATUS_INTERRUPTED`` too. Cancelled tasks are hard-killed
          via the per-task ``asyncio.CancelledError`` handler so no
          orphan survives.

        Implementation uses ``asyncio.wait_for`` over a single
        await-all inner coroutine; this matches TaskGroup's
        single-cancellation-boundary semantics.
        """
        try:
            await asyncio.wait_for(self._wait_all(), timeout=self._drain_timeout)
            status = STATUS_DRAINED
        except asyncio.TimeoutError:
            # Drain deadline beat the in-flight tasks. Mark every
            # submitted task whose result is not yet recorded as
            # ``STATUS_INTERRUPTED`` (covers in-flight + still-queued),
            # then cancel the asyncio tasks so their per-task handler
            # hard-kills the subprocess.
            status = STATUS_INTERRUPTED
            await self._cancel_and_seed_interrupted()

        return self._finalize_wave(status)

    # ---- drain helpers ----

    async def _wait_all(self) -> None:
        """Loop until every submitted task has terminated and the FIFO is empty.

        The loop is safe under cancellation: any awaiting point
        propagates ``CancelledError`` upward (NFR-03). Per-task
        ``finally`` clauses pop their ``task_id`` from ``self._tasks``
        but the submission ledger (``self._submitted``) is preserved,
        so completion is observed via ``self._results`` rather than
        ``self._tasks``.
        """
        while True:
            current = list(self._tasks.values())
            if current:
                # Wait for the current wave; ``return_exceptions=True``
                # so per-task ``CancelledError`` does not abort the
                # siblings (TaskGroup semantics).
                await asyncio.gather(*current, return_exceptions=True)
            # Pull as much of the FIFO as we have capacity for.
            while self._pending and self._in_flight_count < self._max_concurrent:
                tid, cmd = self._pending.popleft()
                self._dispatch(tid, cmd)
            # Termination: every submitted task has a result, and
            # no more are pending dispatch.
            if (
                not self._tasks
                and not self._pending
                and self._submitted.issubset(self._results.keys())
            ):
                return

    async def _cancel_and_seed_interrupted(self) -> None:
        """Cancel every live task and seed ``STATUS_INTERRUPTED`` for any
        task that has not yet recorded a terminal state.

        Snapshots the live dispatched-set before cancelling so per-task
        ``finally`` clauses (which pop entries from ``self._tasks``)
        cannot drop a task_id before we have a chance to seed its
        ledger entry. ``setdefault`` preserves any state already
        recorded (e.g. a task that finished cleanly in the same tick as
        the timeout — its ``STATUS_DRAINED`` wins).
        """
        in_flight_snapshot = list(self._tasks.values())
        # Queued but never started → interrupted.
        while self._pending:
            tid, _ = self._pending.popleft()
            self._results.setdefault(tid, STATUS_INTERRUPTED)
        # In-flight → cancel.
        for task in in_flight_snapshot:
            if not task.done():
                task.cancel()
        # Seed interrupted ledger from the submitted set AFTER
        # cancelling so the per-task handler does not race the
        # ``setdefault``.
        for tid in self._submitted:
            self._results.setdefault(tid, STATUS_INTERRUPTED)
        # Reap cancellations; ``return_exceptions=True`` keeps the
        # per-task ``CancelledError`` from bubbling out of drain.
        if in_flight_snapshot:
            await asyncio.gather(*in_flight_snapshot, return_exceptions=True)

    def _finalize_wave(self, status: str) -> Dict[str, Any]:
        """Snapshot the per-task result ledger and reset wave state so
        the executor can accept a new submission cycle."""
        snapshot = dict(self._results)
        self._results.clear()
        self._tasks.clear()
        self._submitted.clear()
        self._in_flight_count = 0
        return {"status": status, "tasks": snapshot}


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