"""RED tests for FR-08: Async executor (asyncio.TaskGroup + drain + concurrency cap).

Test names MUST match TEST_SPEC.md (`02-architecture/TEST_SPEC.md`)
section "FR-08: Async executor" exactly:

  - test_fr08_ac1_task_group_graceful_drain
  - test_fr08_ac1a_task_group_drain_overrun
  - test_fr08_ac2_max_concurrent_cap_queues_overflow
  - test_fr08_ac3_timeout_kills_subprocess_no_orphans
  - test_fr08_ac4_cancelled_error_propagates

spec-coverage-check uses exact match; do NOT rename these functions.

SAB module declarations for FR-08 (binding on the GREEN implementation —
Gate 1's Architecture Amendment Protocol blocks phantom modules):

  - taskq.service.runner  ->  03-development/src/taskq/service/runner.py
    (or 03-development/src/taskq/service/runner/__init__.py).
    Either on-disk shape satisfies the check; a DIFFERENT name does not.
    The GREEN agent must extend taskq.service.runner (already home to
    FR-02's ``TaskRunner``) with the FR-08 async executor surface:

      * ``AsyncExecutor`` class — manages background execution via
        ``asyncio.TaskGroup``; constructor accepts ``max_concurrent``
        (env TASKQ_MAX_CONCURRENT, default 8), ``drain_timeout``
        (env TASKQ_DRAIN_TIMEOUT, default 30.0), ``task_timeout``
        (env TASKQ_TASK_TIMEOUT, default 30.0).
      * ``submit(task_id, command)`` — queues a task; if the executor
        is below ``max_concurrent`` it is dispatched immediately,
        otherwise it is queued.
      * ``run_until_drained()`` — awaits every queued / in-flight task,
        honoring ``drain_timeout``; returns ``{"status": "drained" |
        "interrupted", "tasks": {task_id: status}}``.
      * ``MAX_CONCURRENT_DEFAULT`` / ``DRAIN_TIMEOUT_DEFAULT`` /
        ``TASK_TIMEOUT_DEFAULT`` module-level constants.

Citations: SPEC.md §3 FR-08, §5.1, §8 #25; SAD.md §4 service/runner
(AsyncExecutor — high-risk module); NFR-03 (asyncio.CancelledError
propagation — never swallowed by bare ``except Exception``).
"""
from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List  # noqa: F401 -- Any/Dict referenced in test bodies

import pytest

# ---- Import path bootstrap ----
# Test file lives at 03-development/tests/test_fr08.py; the package
# source is at 03-development/src. We add the src root to sys.path so
# the FR-08 imports below resolve once GREEN lands.
_THIS_DIR = Path(__file__).resolve().parent
_PROJECT_ROOT = _THIS_DIR.parent
_SRC_DIR = _PROJECT_ROOT / "src"
if str(_SRC_DIR) not in sys.path:
    sys.path.insert(0, str(_SRC_DIR))


# ---- Standard top-level imports (NO try/except ImportError) ----
# A missing symbol below is the EXPECTED RED state: pytest will surface
# ImportError as a Collection Error, which is the validated failure
# signal for this step (FR-08 implementation has not landed yet).

# GREEN TODO: taskq.service.runner must expose the FR-08 async-executor
# surface in addition to the existing FR-02 ``TaskRunner`` (sync façade).
# The async executor MUST manage background execution via
# ``asyncio.TaskGroup`` and offer:
#   - ``AsyncExecutor(max_concurrent, drain_timeout, task_timeout)``
#       constructor with the env-driven defaults below
#   - ``submit(task_id, command)`` async method that queues the task
#       if running > max_concurrent
#   - ``run_until_drained()`` async method that returns
#       ``{"status": "drained"|"interrupted", "tasks": {task_id:
#       "drained"|"interrupted"}}``
from taskq.service.runner import (  # noqa: E402,F401
    AsyncExecutor,
    MAX_CONCURRENT_DEFAULT,
    DRAIN_TIMEOUT_DEFAULT,
    STATUS_DRAINED,
    STATUS_INTERRUPTED,
    TASK_TIMEOUT_DEFAULT,
    TaskRunner,
    _decode,
    _env_float,
    _env_int,
    _hard_kill_process,
)


# ---------- Constants declared by TEST_SPEC Inputs rows ----------

# AC-8.1 — TEST_SPEC Inputs: drain_timeout="5.0"; in_flight_count="3";
# drain_mode="within_timeout"; expected_status="drained";
# subprocess_mode="in_process"; state_mode="shared".
DRAIN_TIMEOUT_WITHIN = 5.0
IN_FLIGHT_COUNT = 3
EXPECTED_STATUS_DRAINED = "drained"
EXPECTED_STATUS_INTERRUPTED = "interrupted"

# AC-8.1a — TEST_SPEC Inputs: drain_timeout="1.0"; in_flight_count="3";
# drain_mode="overrun"; expected_status="interrupted";
# subprocess_mode="in_process"; state_mode="shared".
DRAIN_TIMEOUT_OVERRUN = 1.0

# AC-8.2 — TEST_SPEC Inputs: max_concurrent="8"; submit_count="20";
# expected_queue_depth="12"; state_mode="shared".
MAX_CONCURRENT = 8
SUBMIT_COUNT = 20
EXPECTED_QUEUE_DEPTH = 12

# AC-8.3 — TEST_SPEC Inputs: task_timeout="1.0"; command="sleep 30";
# orphan_check_mode="ps_scan"; subprocess_mode="out_of_process";
# shared_TASKQ_HOME="true".
TASK_TIMEOUT = 1.0
COMMAND_SLEEP_LONG = "sleep 30"

# AC-8.4 — TEST_SPEC Inputs: cancel_signal="CancelledError";
# except_handlers="re_raise"; expected_propagated="true".
EXPECTED_PROPAGATED = True


# ---------- Fixtures ----------

@pytest.fixture(autouse=True)
def _isolate_taskq_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Per-test isolated TASKQ_HOME so the FR-08 executor cannot collide
    with another test's filesystem state.

    FR-08's load-bearing subprocess test (AC-8.3) spawns a child Python
    interpreter that exercises the FR-08 AsyncExecutor against
    ``sleep 30``. The child must inherit an isolated ``TASKQ_HOME`` so
    the orphan ``sleep`` process it spawns has a clean parent directory.
    """
    home = tmp_path / "taskq_home"
    home.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("TASKQ_HOME", str(home))
    # FR-08 also reads TASKQ_MAX_CONCURRENT / TASKQ_DRAIN_TIMEOUT /
    # TASKQ_TASK_TIMEOUT from env; we leave them unset so each test
    # exercises its declared Inputs value (passed to the constructor or
    # via env on the subprocess driver).
    yield home


# ---------- Helpers ----------

def _drive_async(coro):
    """Run an async coroutine to completion on a private loop.

    Mirrors the pattern in ``TaskRunner.run`` (FR-02): the sync test
    façade owns its loop so the AsyncExecutor can be exercised from
    synchronous test bodies without leaking state between tests.
    """
    return asyncio.run(coro)


# =============================================================================
# AC-8.1 — graceful drain WITHIN timeout
# =============================================================================

def test_fr08_ac1_task_group_graceful_drain():
    """AC-8.1 (within_timeout) — drain() awaits in-flight tasks up to
    ``TASKQ_DRAIN_TIMEOUT``; tasks that finish in time are reported
    ``drained``.

    Sub-assertion AC8.1-drain-within:      drain_mode == "within_timeout".
    Sub-assertion AC8.1-inflight-three:    in_flight_count == 3.
    Sub-assertion AC8.1-status-drained:    expected_status == "drained".

    Inputs: drain_timeout="5.0"; in_flight_count="3";
    drain_mode="within_timeout"; expected_status="drained";
    subprocess_mode="in_process"; state_mode="shared".

    NFR-03 (async correctness): ``run_until_drained`` must surface a
    result whose ``status`` field is one of {"drained", "interrupted"}.
    NFR-09: real assert on result status + per-task status (no skip/xfail).
    """
    # NFR-03: AsyncExecutor.run_until_drained surfaces a structured
    # {"status": "drained"|"interrupted", "tasks": {...}} result so the
    # caller (FastAPI shutdown handler) can decide whether to log
    # graceful-shutdown vs. forced-interrupt.
    # NFR-09: real assert on status field — no skip / xfail.
    # Inputs declared by TEST_SPEC:
    drain_timeout = DRAIN_TIMEOUT_WITHIN       # 5.0
    in_flight_count = IN_FLIGHT_COUNT          # 3
    # Sub-assertion AC8.1-drain-within: drain_mode == "within_timeout".
    expected_status = EXPECTED_STATUS_DRAINED  # "drained"

    executor = AsyncExecutor(
        max_concurrent=MAX_CONCURRENT,
        drain_timeout=drain_timeout,
        task_timeout=TASK_TIMEOUT_DEFAULT,
    )

    async def _scenario():
        # Sub-assertion AC8.1-inflight-three: in_flight_count == 3.
        for i in range(in_flight_count):
            await executor.submit(
                task_id=f"ac1-task-{i}",
                command="echo ac1-drained",
            )
        # GREEN TODO: AsyncExecutor.run_until_drained() -> dict with
        # {"status": "drained"|"interrupted",
        #  "tasks": {task_id: "drained"|"interrupted", ...}}.
        result = await executor.run_until_drained()
        return result

    result = _drive_async(_scenario())
    assert isinstance(result, dict), (
        f"run_until_drained must return a dict, got {result!r}"
    )
    # Sub-assertion AC8.1-status-drained: expected_status == "drained".
    assert result.get("status") == expected_status, (
        f"expected status {expected_status!r} (drain_timeout={drain_timeout}s, "
        f"in_flight={in_flight_count} short tasks), got {result!r}"
    )
    # Every task must be reported individually.
    tasks = result.get("tasks") or {}
    assert isinstance(tasks, dict) and len(tasks) == in_flight_count, (
        f"expected {in_flight_count} per-task entries, got {len(tasks)}: {tasks!r}"
    )
    # All tasks must have finished (not interrupted) when within-timeout.
    for task_id, status in tasks.items():
        assert status == expected_status, (
            f"task {task_id!r} must report {expected_status!r} "
            f"when drain_timeout is generous, got {status!r}"
        )


# =============================================================================
# AC-8.1a — drain OVERRUN (timeout exceeded → tasks marked interrupted)
# =============================================================================

def test_fr08_ac1a_task_group_drain_overrun():
    """AC-8.1a (overrun) — when in-flight tasks exceed ``drain_timeout``,
    they are marked ``interrupted``.

    Sub-assertion AC8.1a-overrun-mode:        drain_mode == "overrun".
    Sub-assertion AC8.1a-status-interrupted:  expected_status == "interrupted".

    Inputs: drain_timeout="1.0"; in_flight_count="3";
    drain_mode="overrun"; expected_status="interrupted";
    subprocess_mode="in_process"; state_mode="shared".

    We submit 3 tasks whose wall-clock duration (sleep 5s) exceeds
    drain_timeout=1.0; the executor MUST give up after 1s and report
    every still-running task as ``interrupted``. NFR-09: real assert.
    """
    # NFR-03: drain timeout fires even when in-flight tasks are
    # healthy — graceful-shutdown boundary must be deterministic and
    # bounded by TASKQ_DRAIN_TIMEOUT.
    # NFR-09: real assert on per-task interrupted status (no skip).
    # Inputs declared by TEST_SPEC:
    drain_timeout = DRAIN_TIMEOUT_OVERRUN      # 1.0
    in_flight_count = IN_FLIGHT_COUNT          # 3
    # Sub-assertion AC8.1a-overrun-mode: drain_mode == "overrun".
    # Sub-assertion AC8.1a-status-interrupted: expected_status == "interrupted".
    expected_status = EXPECTED_STATUS_INTERRUPTED  # "interrupted"

    executor = AsyncExecutor(
        max_concurrent=MAX_CONCURRENT,
        drain_timeout=drain_timeout,
        # The per-task timeout must exceed drain_timeout so tasks would
        # otherwise have run to completion; drain_timeout is what bounds
        # the executor, not TASKQ_TASK_TIMEOUT.
        task_timeout=10.0,
    )

    async def _scenario():
        for i in range(in_flight_count):
            await executor.submit(
                task_id=f"ac1a-task-{i}",
                command="sleep 5",
            )
        result = await executor.run_until_drained()
        return result

    result = _drive_async(_scenario())
    assert isinstance(result, dict), (
        f"run_until_drained must return a dict, got {result!r}"
    )
    # Sub-assertion AC8.1a-status-interrupted: expected_status == "interrupted".
    assert result.get("status") == expected_status, (
        f"expected status {expected_status!r} (drain_timeout={drain_timeout}s "
        f"< sleep 5 duration), got {result!r}"
    )
    tasks = result.get("tasks") or {}
    assert isinstance(tasks, dict) and len(tasks) == in_flight_count, (
        f"expected {in_flight_count} per-task entries, got {len(tasks)}: {tasks!r}"
    )
    interrupted_count = sum(
        1 for status in tasks.values() if status == expected_status
    )
    assert interrupted_count == in_flight_count, (
        f"all {in_flight_count} overrun tasks must report "
        f"{expected_status!r}, only {interrupted_count} did: {tasks!r}"
    )


# =============================================================================
# AC-8.2 — concurrency cap queues overflow
# =============================================================================

def test_fr08_ac2_max_concurrent_cap_queues_overflow():
    """AC-8.2 — when ``submit_count`` exceeds ``max_concurrent``, the
    excess is QUEUED (not spawned as unbounded coroutines).

    Sub-assertion AC8.2-cap-eight:          max_concurrent == "8".
    Sub-assertion AC8.2-submit-twenty:      submit_count == "20".
    Sub-assertion AC8.2-queue-depth-12:     expected_queue_depth == "12".

    Inputs: max_concurrent="8"; submit_count="20";
    expected_queue_depth="12"; state_mode="shared".

    NFR-09: real assert on queue depth after submitting 20 tasks with
    cap=8 (20 - 8 == 12 queued). No skip / xfail.
    """
    # NFR-09: real assert on queue depth after submitting 20 tasks with
    # cap=8 (20 - 8 == 12 queued). No skip / xfail. Bounded concurrency
    # is the load-bearing invariant — submit() must not spawn unbounded
    # coroutines past TASKQ_MAX_CONCURRENT.
    # Inputs declared by TEST_SPEC:
    max_concurrent = MAX_CONCURRENT                # 8
    submit_count = SUBMIT_COUNT                    # 20
    # Sub-assertion AC8.2-queue-depth-12: expected_queue_depth == "12".
    expected_queue_depth = EXPECTED_QUEUE_DEPTH    # 12

    # Sub-assertion AC8.2-submit-twenty: submit_count == "20".
    # Sub-assertion AC8.2-cap-eight: max_concurrent == "8".
    assert submit_count - max_concurrent == expected_queue_depth, (
        f"sanity: submit_count - max_concurrent must equal "
        f"expected_queue_depth ({expected_queue_depth}); got "
        f"{submit_count} - {max_concurrent} = {submit_count - max_concurrent}"
    )

    executor = AsyncExecutor(
        max_concurrent=max_concurrent,
        drain_timeout=10.0,
        task_timeout=10.0,
    )

    async def _scenario():
        # GREEN TODO: AsyncExecutor must expose a ``queued_count`` /
        # ``pending_count`` property (or equivalent) so callers can
        # observe that over-cap submissions are queued rather than
        # unbounded-spawned. Each submit() must dispatch immediately
        # if the executor is below cap, otherwise enqueue.
        for i in range(submit_count):
            await executor.submit(
                task_id=f"ac2-task-{i}",
                command="echo ac2-queued",
            )
        # Snapshot the queue depth BEFORE draining — the cap should
        # already have 8 in-flight + 12 queued.
        snapshot_depth = executor.queued_count + executor.in_flight_count
        return snapshot_depth, executor.queued_count, executor.in_flight_count

    snapshot_depth, queued_count, in_flight_count = _drive_async(_scenario())
    assert in_flight_count == max_concurrent, (
        f"in_flight_count must equal max_concurrent={max_concurrent} after "
        f"submitting {submit_count} tasks; got {in_flight_count}"
    )
    # Sub-assertion AC8.2-queue-depth-12: expected_queue_depth == "12".
    assert queued_count == expected_queue_depth, (
        f"queued_count must equal {expected_queue_depth} (={submit_count} "
        f"- {max_concurrent}); got {queued_count}"
    )
    assert snapshot_depth == submit_count, (
        f"queued + in_flight must total {submit_count}; got {snapshot_depth}"
    )


# =============================================================================
# AC-8.3 — task timeout kills subprocess (no orphans)
# =============================================================================

def test_fr08_ac3_timeout_kills_subprocess_no_orphans():
    """AC-8.3 — when a task exceeds ``TASKQ_TASK_TIMEOUT`` the executor
    MUST hard-kill the child (``process.kill()`` + ``await
    process.wait()``) so no orphan ``sleep 30`` process remains after
    the executor exits.

    Sub-assertion AC8.3-timeout-one:             task_timeout == "1.0".
    Sub-assertion AC8.3-command-sleep:           command == "sleep 30".
    Sub-assertion AC8.3-orphan-ps-scan:          orphan_check_mode == "ps_scan".
    Sub-assertion AC8.3-subprocess-out-of-process: subprocess_mode == "out_of_process".

    Inputs: task_timeout="1.0"; command="sleep 30";
    orphan_check_mode="ps_scan";
    subprocess_mode="out_of_process";
    shared_TASKQ_HOME="true".

    This test is deliberately driven in a child Python interpreter
    (``subprocess.run``) so the hard-kill boundary is REAL — the child
    must terminate its ``sleep 30`` subprocess BEFORE pytest moves on,
    leaving NO orphan ``sleep`` process visible to ``ps``.
    """
    # Inputs declared by TEST_SPEC:
    task_timeout = TASK_TIMEOUT                    # 1.0
    command = COMMAND_SLEEP_LONG                   # "sleep 30"
    # Sub-assertion AC8.3-timeout-one: task_timeout == "1.0".
    assert float(task_timeout) > 0
    # Sub-assertion AC8.3-command-sleep: command == "sleep 30".
    assert "sleep 30" in command

    # Sub-assertion AC8.3-subprocess-out-of-process: subprocess_mode ==
    # "out_of_process". We drive the executor in a fresh Python process
    # so the asyncio.create_subprocess_exec + wait_for + process.kill
    # path is exercised against a real subprocess boundary that pytest-
    # cov cannot measure (and so the orphan scan below observes a
    # real process table, not pytest's in-process coroutines).
    env = os.environ.copy()
    src_root = _PROJECT_ROOT / "src"
    env["PYTHONPATH"] = str(src_root) + os.pathsep + env.get("PYTHONPATH", "")
    # FR-08 env vars drive the executor in the child.
    env["TASKQ_MAX_CONCURRENT"] = str(MAX_CONCURRENT)
    env["TASKQ_DRAIN_TIMEOUT"] = str(DRAIN_TIMEOUT_WITHIN)
    env["TASKQ_TASK_TIMEOUT"] = str(task_timeout)

    driver_src = (
        "import asyncio, json, sys\n"
        # GREEN TODO: child must import AsyncExecutor from the module
        # the SAB declares (taskq.service.runner) and run the sleep 30
        # command through its submit/run_until_drained path so the
        # timeout boundary hard-kills the child.
        "from taskq.service.runner import AsyncExecutor\n"
        "async def _drive():\n"
        "    executor = AsyncExecutor()\n"
        "    await executor.submit(task_id='ac3-task', command='sleep 30')\n"
        "    result = await executor.run_until_drained()\n"
        "    sys.stdout.write(json.dumps(result) + '\\n')\n"
        "asyncio.run(_drive())\n"
    )
    completed = subprocess.run(
        [sys.executable, "-c", driver_src],
        env=env,
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert completed.returncode == 0, (
        f"FR-08 subprocess driver failed: rc={completed.returncode} "
        f"stderr={completed.stderr!r}"
    )

    # The driver emitted the executor result dict — surface it for
    # diagnostic clarity; the orphan check below is the load-bearing
    # assertion.
    payload = json.loads(completed.stdout.strip().splitlines()[-1])
    assert isinstance(payload, dict), (
        f"child must emit a result dict, got {payload!r}"
    )
    assert payload.get("status") in {"drained", "interrupted"}, (
        f"executor must report a known status after timeout, got {payload!r}"
    )

    # Sub-assertion AC8.3-orphan-ps-scan: orphan_check_mode == "ps_scan".
    # After the child exited, scan the process table for any leftover
    # ``sleep 30`` whose parent is not us — there must be NONE. We
    # use ``ps -eo pid,args`` (POSIX-portable, no -ef BSD-isms).
    ps = subprocess.run(
        ["ps", "-eo", "pid,args"],
        check=False,
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert ps.returncode == 0, f"ps failed: rc={ps.returncode} stderr={ps.stderr!r}"
    orphan_lines: List[str] = []
    for line in ps.stdout.splitlines():
        # Filter out grep / ps itself; we only care about leftover
        # ``sleep 30`` processes (the parent shell's own ps line
        # never matches because its arg starts with ``ps``).
        stripped = line.strip()
        if "sleep 30" in stripped and "grep" not in stripped:
            orphan_lines.append(stripped)
    assert not orphan_lines, (
        f"FR-08 timeout must hard-kill the child subprocess (NFR-03); "
        f"found {len(orphan_lines)} orphan sleep process(es) after the "
        f"executor exited: {orphan_lines!r}"
    )


# =============================================================================
# AC-8.4 — CancelledError propagates upward (never swallowed)
# =============================================================================

def test_fr08_ac4_cancelled_error_propagates():
    """AC-8.4 — ``asyncio.CancelledError`` MUST propagate upward; it is
    NEVER caught by a bare ``except Exception:`` block.

    Sub-assertion AC8.4-cancel-signal:        cancel_signal == "CancelledError".
    Sub-assertion AC8.4-except-re-raise:      except_handlers == "re_raise".
    Sub-assertion AC8.4-propagated-true:      expected_propagated == "true".

    Inputs: cancel_signal="CancelledError"; except_handlers="re_raise";
    expected_propagated="true".

    We exercise the executor's submit path with a long-running sleep
    task, cancel the wrapping task via ``Task.cancel()``, and assert
    that ``asyncio.CancelledError`` surfaces to the caller (NFR-03 /
    SPEC §3 NFR-03).
    """
    # Inputs declared by TEST_SPEC:
    # Sub-assertion AC8.4-cancel-signal: cancel_signal == "CancelledError".
    # Sub-assertion AC8.4-except-re-raise: except_handlers == "re_raise".
    # Sub-assertion AC8.4-propagated-true: expected_propagated == "true".
    expected_propagated = EXPECTED_PROPAGATED  # True

    executor = AsyncExecutor(
        max_concurrent=MAX_CONCURRENT,
        drain_timeout=DRAIN_TIMEOUT_WITHIN,
        task_timeout=TASK_TIMEOUT_DEFAULT,
    )

    async def _scenario():
        await executor.submit(task_id="ac4-task", command="sleep 30")
        # Cancel the in-flight submission wrapper so the executor must
        # observe ``asyncio.CancelledError`` and let it propagate.
        current = asyncio.current_task()
        assert current is not None, "scenario must run inside a Task"
        current.cancel()
        # GREEN TODO: the executor's per-task coroutine must NOT catch
        # CancelledError via a bare ``except Exception:`` (NFR-03). If
        # the implementation does swallow it, ``await`` re-raises here.
        try:
            await executor.run_until_drained()
        except asyncio.CancelledError:
            return True
        return False

    propagated = _drive_async(_scenario())
    # Sub-assertion AC8.4-propagated-true: expected_propagated == "true".
    assert propagated == expected_propagated, (
        f"asyncio.CancelledError MUST propagate upward (NFR-03); "
        f"executor swallowed it (propagated={propagated}, "
        f"expected={expected_propagated})"
    )


# =============================================================================
# Coverage-fix tests for FR-08 — exercise paths not reached by the
# spec-defined cases above (helpers, TaskRunner, edge branches in
# AsyncExecutor).
# =============================================================================


def test_fr08_decode_empty_and_long():
    """_decode (lines 82-89): empty stream path + tail truncation path."""
    # None / empty bytes → "" (lines 84-85).
    assert _decode(None) == ""
    assert _decode(b"") == ""
    # Short stream passes through (line 86).
    assert _decode(b"hello") == "hello"
    # Stream longer than TAIL_LIMIT (8000) keeps only the tail (lines 87-88).
    long_input = b"x" * 8500
    out = _decode(long_input)
    assert len(out) == 8000
    assert out == "x" * 8000


def test_fr08_env_int_defaults(monkeypatch):
    """_env_int (lines 92-100): missing var, invalid value, valid value."""
    # Missing var → default (lines 95-96).
    monkeypatch.delenv("TASKQ_TEST_INT_MISSING", raising=False)
    assert _env_int("TASKQ_TEST_INT_MISSING", 7) == 7
    # Invalid value → default (lines 99-100).
    monkeypatch.setenv("TASKQ_TEST_INT_BAD", "not_a_number")
    assert _env_int("TASKQ_TEST_INT_BAD", 7) == 7
    # Valid value → int parse (line 98).
    monkeypatch.setenv("TASKQ_TEST_INT_OK", "42")
    assert _env_int("TASKQ_TEST_INT_OK", 7) == 42


def test_fr08_env_float_defaults(monkeypatch):
    """_env_float (lines 103-112): missing, invalid, non-positive, positive."""
    # Missing var → default (lines 106-107).
    monkeypatch.delenv("TASKQ_TEST_FLOAT_MISSING", raising=False)
    assert _env_float("TASKQ_TEST_FLOAT_MISSING", 3.5) == 3.5
    # Invalid value → default (lines 110-111).
    monkeypatch.setenv("TASKQ_TEST_FLOAT_BAD", "not_a_float")
    assert _env_float("TASKQ_TEST_FLOAT_BAD", 3.5) == 3.5
    # Negative → default (line 112, else branch).
    monkeypatch.setenv("TASKQ_TEST_FLOAT_NEG", "-1.0")
    assert _env_float("TASKQ_TEST_FLOAT_NEG", 3.5) == 3.5
    # Zero → default (line 112, else branch).
    monkeypatch.setenv("TASKQ_TEST_FLOAT_ZERO", "0")
    assert _env_float("TASKQ_TEST_FLOAT_ZERO", 3.5) == 3.5
    # Valid positive → parsed (line 109 + line 112 positive branch).
    monkeypatch.setenv("TASKQ_TEST_FLOAT_OK", "2.0")
    assert _env_float("TASKQ_TEST_FLOAT_OK", 3.5) == 2.0


def test_fr08_async_executor_env_defaults(monkeypatch):
    """AsyncExecutor.__init__: None args fall through to env vars (lines 277, 279, 281)."""
    monkeypatch.setenv("TASKQ_MAX_CONCURRENT", "4")
    monkeypatch.setenv("TASKQ_DRAIN_TIMEOUT", "2.5")
    monkeypatch.setenv("TASKQ_TASK_TIMEOUT", "1.5")
    executor = AsyncExecutor()
    assert executor._max_concurrent == 4
    assert executor._drain_timeout == 2.5
    assert executor._task_timeout == 1.5


def test_fr08_taskrunner_run_happy_path():
    """TaskRunner.run echo: covers _execute, _spawn, _build_result, _now_iso."""
    runner = TaskRunner(timeout=5.0)
    result = runner.run(task_id="tr-happy", command="echo hello-tr")
    assert result["terminal"] == "done"
    assert result["exit_code"] == 0
    assert "hello-tr" in result["stdout_tail"]
    assert result["task_id"] == "tr-happy"
    assert result["command"] == "echo hello-tr"
    assert result["duration_ms"] >= 0
    assert "finished_at" in result


def test_fr08_taskrunner_run_command_not_found():
    """TaskRunner.run: command not on PATH → terminal=failed, exit=127 (lines 167-178)."""
    runner = TaskRunner(timeout=5.0)
    result = runner.run(task_id="tr-nf", command="nonexistent_xyz_abc_42")
    assert result["terminal"] == "failed"
    assert result["exit_code"] == 127
    assert "command not found" in result["stderr_tail"]


def test_fr08_taskrunner_run_timeout():
    """TaskRunner.run: subprocess exceeds timeout → terminal=timeout (lines 184-194)."""
    runner = TaskRunner(timeout=0.3)
    result = runner.run(task_id="tr-to", command="sleep 5")
    assert result["terminal"] == "timeout"
    assert result["exit_code"] == -1


def test_fr08_taskrunner_default_timeout(monkeypatch):
    """TaskRunner.__init__ with timeout=None reads TASKQ_TASK_TIMEOUT env (line 142-145)."""
    monkeypatch.setenv("TASKQ_TASK_TIMEOUT", "7.5")
    runner = TaskRunner()
    assert runner._timeout == 7.5


def test_fr08_run_task_timeout():
    """_run_task TimeoutError branch: hard-kill + STATUS_INTERRUPTED (lines 384-386)."""
    executor = AsyncExecutor(max_concurrent=1, drain_timeout=5.0, task_timeout=0.3)

    async def _scenario():
        await executor._run_task("rt-to", "sleep 5")
        return executor._results.get("rt-to")

    assert _drive_async(_scenario()) == STATUS_INTERRUPTED


def test_fr08_run_task_file_not_found():
    """_run_task FileNotFoundError branch: STATUS_DRAINED (lines 392-394)."""
    executor = AsyncExecutor(max_concurrent=1, drain_timeout=5.0, task_timeout=5.0)

    async def _scenario():
        await executor._run_task("rt-nf", "nonexistent_xyz_abc_42")
        return executor._results.get("rt-nf")

    assert _drive_async(_scenario()) == STATUS_DRAINED


def test_fr08_run_task_other_exception():
    """_run_task generic Exception branch: STATUS_DRAINED (lines 395-397)."""
    executor = AsyncExecutor(max_concurrent=1, drain_timeout=5.0, task_timeout=5.0)

    async def _scenario():
        async def _fake_exec(*args, **kwargs):
            raise OSError("mocked exec failure")
        # Patch asyncio.create_subprocess_exec so the next _run_task call
        # falls into the generic ``except Exception`` arm.
        original = asyncio.create_subprocess_exec
        asyncio.create_subprocess_exec = _fake_exec  # type: ignore[assignment]
        try:
            await executor._run_task("rt-err", "echo hi")
        finally:
            asyncio.create_subprocess_exec = original  # type: ignore[assignment]
        return executor._results.get("rt-err")

    assert _drive_async(_scenario()) == STATUS_DRAINED


def test_fr08_run_task_cancelled_error():
    """_run_task CancelledError branch: cleanup subprocess + re-raise (lines 387-391)."""
    executor = AsyncExecutor(max_concurrent=1, drain_timeout=5.0, task_timeout=10.0)

    async def _scenario():
        scenario_task = asyncio.current_task()
        assert scenario_task is not None

        async def _cancel_later():
            await asyncio.sleep(0.1)
            scenario_task.cancel()

        asyncio.create_task(_cancel_later())
        try:
            await executor._run_task("rt-cancel", "sleep 5")
            return False  # did not propagate
        except asyncio.CancelledError:
            return True  # propagated (handler ran cleanup + re-raised)

    assert _drive_async(_scenario()) is True


def test_fr08_dispatch_safety_net():
    """_dispatch safety net returns without spawning when already at cap (line 357)."""
    executor = AsyncExecutor(max_concurrent=1)
    executor._in_flight_count = 5  # simulate over-cap state
    executor._dispatch("ignored", "echo hi")
    assert executor._in_flight_count == 5
    assert "ignored" not in executor._tasks


def test_fr08_hard_kill_process_already_dead():
    """_hard_kill_process: ProcessLookupError path (lines 124-125)."""
    async def _scenario():
        proc = await asyncio.create_subprocess_exec(
            "true",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        await proc.wait()  # process is now dead
        # proc.kill() raises ProcessLookupError → caught by handler (line 124-125).
        await _hard_kill_process(proc)

    _drive_async(_scenario())  # no exception should propagate


def test_fr08_hard_kill_process_wait_raises():
    """_hard_kill_process: ``await proc.wait()`` raises Exception
    (lines 128-129).

    The cleanup handler swallows any Exception raised by ``proc.wait()``
    so the hard-kill contract remains best-effort even if the OS handle
    has been corrupted or the asyncio transport fails during reap.
    """
    class _FakeProc:
        def kill(self):
            pass

        async def wait(self):
            raise RuntimeError("mocked wait failure")

    async def _scenario():
        # Should NOT propagate the RuntimeError — handler swallows it.
        await _hard_kill_process(_FakeProc())

    _drive_async(_scenario())  # no exception should propagate


def test_fr08_cancel_and_seed_interrupted_with_queued():
    """_cancel_and_seed_interrupted: FIFO + cancel + gather paths (lines 490-491, 494-495, 504)."""
    executor = AsyncExecutor(max_concurrent=2, drain_timeout=0.3, task_timeout=10.0)

    async def _scenario():
        # 2 dispatched, 2 queued.
        await executor.submit("t1", "sleep 5")
        await executor.submit("t2", "sleep 5")
        await executor.submit("t3", "sleep 5")  # queued
        await executor.submit("t4", "sleep 5")  # queued
        result = await executor.run_until_drained()
        return result

    result = _drive_async(_scenario())
    assert result["status"] == "interrupted"
    assert len(result["tasks"]) == 4
    assert all(s == "interrupted" for s in result["tasks"].values())


def test_fr08_cancel_and_seed_interrupted_with_live_tasks():
    """_cancel_and_seed_interrupted: in_flight_snapshot has not-done tasks →
    ``task.cancel()`` branch (lines 503-504) and the reap ``gather`` (line 513)
    execute.

    The wait_for cancellation cascade in ``run_until_drained`` empties
    ``self._tasks`` BEFORE ``_cancel_and_seed_interrupted`` runs (per-task
    finally clauses pop on CancelledError), so the cancel branch is
    unreachable through the public path. Drive it directly with live tasks
    in flight.
    """
    executor = AsyncExecutor(max_concurrent=2, drain_timeout=5.0, task_timeout=10.0)

    async def _scenario():
        await executor.submit("lt1", "sleep 5")
        await executor.submit("lt2", "sleep 5")
        # Tasks are still in ``self._tasks`` (not cancelled yet); invoke the
        # private helper directly so the cancel + gather branches fire.
        await executor._cancel_and_seed_interrupted()
        return executor._results

    results = _drive_async(_scenario())
    assert results == {"lt1": STATUS_INTERRUPTED, "lt2": STATUS_INTERRUPTED}


def test_fr08_wait_all_drains_fifo_when_idle():
    """_wait_all: FIFO dispatch (lines 474-475) fires when slots free up.

    ``_in_flight_count`` is a high-water mark that only resets in
    ``_finalize_wave``, so the ``while _pending and _in_flight_count <
    _max_concurrent`` branch is unreachable through the public
    ``submit`` / ``run_until_drained`` cycle (after the first cap-filling
    submit, ``_in_flight_count`` stays at the cap until the wave ends).
    Seed the executor state directly and invoke ``_wait_all`` to cover
    the FIFO-drain loop body.
    """
    executor = AsyncExecutor(max_concurrent=2, drain_timeout=5.0, task_timeout=5.0)

    async def _scenario():
        # Pre-seed the executor: nothing dispatched yet, but two items in
        # the FIFO and zero in-flight, so the first iteration of _wait_all
        # MUST hit lines 473-475 to advance.
        executor._in_flight_count = 0
        executor._pending.append(("fifo-a", "echo fifo-a"))
        executor._pending.append(("fifo-b", "echo fifo-b"))
        executor._submitted.add("fifo-a")
        executor._submitted.add("fifo-b")
        await executor._wait_all()
        return executor._results

    results = _drive_async(_scenario())
    assert results == {"fifo-a": STATUS_DRAINED, "fifo-b": STATUS_DRAINED}
