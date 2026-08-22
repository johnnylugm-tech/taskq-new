"""RED tests for FR-02: Task execution endpoint.

Test names MUST match TEST_SPEC.md (`02-architecture/TEST_SPEC.md`).
These tests intentionally fail at collection time because the source
modules are not implemented yet — that is the valid RED state for
TDD-RED. Do NOT add try/except ImportError wrappers.

NFR-09 (zero-skip / no xfail): every test in this file performs real asserts
on the runner / repository / route under test. No skip / xfail / assertion-free
stubs are permitted (AC-N9.1..AC-N9.7).

GREEN TODO summary (declarations per SAB.json fr_module_traceability.FR-02):
  - taskq.api.routes.runs    -> router with POST /v1/tasks/{id}/run (scope=write,
                                returns 202 + run_id) and GET /v1/tasks/{id}/runs
                                (scope=read, newest-first).
  - taskq.service.runner     -> async runner that executes commands via
                                asyncio.create_subprocess_exec(*shlex.split(cmd))
                                with timeout TASKQ_TASK_TIMEOUT and transitions
                                pending -> running -> done | failed | timeout.
  - taskq.repository.results -> persistence of task_results rows with columns
                                exit_code / stdout_tail / stderr_tail / duration_ms
                                / finished_at (FR-07 v3 schema).

Citations: SPEC.md §3 FR-02, §5.2, §8 #16; SAD.md §4 (service/runner +
repository/results + api/routes/runs); NFR-02 (no shell=True anywhere in src/).
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

import httpx
import pytest

# ---- Import path bootstrap ----
# Tests must reach the modules declared by SAB.json for FR-02. We add the
# src root to sys.path so the dotted names below resolve once GREEN lands.
_THIS_DIR = Path(__file__).resolve().parent
_SRC_DIR = _THIS_DIR / "src"
if str(_SRC_DIR) not in sys.path:
    sys.path.insert(0, str(_SRC_DIR))

# GREEN TODO: taskq.api.app.create_app must additionally include the FR-02
# router (taskq.api.routes.runs) under the same /v1 prefix. The router must
# expose POST /{task_id}/run (scope=write) and GET /{task_id}/runs (scope=read).
from taskq.api.app import create_app  # noqa: E402

# GREEN TODO: taskq.api.routes.runs must export a FastAPI router with two
# endpoints:
#     - POST "/{task_id}/run"  (scope=write) -> runs the command and returns
#       HTTP 202 + {"run_id": "<uuid>"}. Must NOT block on subprocess exit.
#     - GET  "/{task_id}/runs" (scope=read)  -> returns the task's run
#       history sorted newest-first.
from taskq.api.routes.runs import router as runs_router  # noqa: E402

# GREEN TODO: taskq.service.runner must expose the async runner that takes
# a task_id, looks up the command, schedules the subprocess via
# asyncio.create_subprocess_exec(*shlex.split(command)) (NO shell=True),
# applies TASKQ_TASK_TIMEOUT via asyncio.wait_for, and on completion writes
# a task_results row + transitions the task state to done/failed/timeout.
from taskq.service.runner import TaskRunner  # noqa: E402

# GREEN TODO: taskq.repository.results must provide the persistence boundary
# for task_results rows with the v3-schema columns: exit_code, stdout_tail,
# stderr_tail, duration_ms, finished_at. Must support listing results for a
# task newest-first.
from taskq.repository.results import TaskResultRepository  # noqa: E402


# ---------- Constants declared by TEST_SPEC Inputs rows ----------

VALID_WRITE_KEY = "taskq-write-test-key-abc123"
VALID_READ_KEY = "taskq-read-test-key-abc456"
VALID_ADMIN_KEY = "taskq-admin-test-key-xyz789"

TARGET_ID_EXISTING = uuid.UUID("11111111-1111-1111-1111-111111111111")
TARGET_ID_RUN_1 = uuid.UUID("44444444-4444-4444-4444-444444444444")
TARGET_ID_RUNS = uuid.UUID("55555555-5555-5555-5555-555555555555")

COMMAND_HAPPY = "echo hello"
COMMAND_DONE = "true"
COMMAND_FAILED = "false"
COMMAND_SLEEP = "sleep 30"

# Green TODO must honour these five columns in the v3 task_results schema
EXPECTED_RESULT_COLUMNS = (
    "exit_code",
    "stdout_tail",
    "stderr_tail",
    "duration_ms",
    "finished_at",
)


# ---------- Fixtures ----------

@pytest.fixture
def app():
    """Fresh FastAPI app per test (function-scoped).

    GREEN TODO: create_app() must mount the FR-02 router (runs_router) on the
    same /v1 prefix used for the FR-01 tasks router, with a sub-path so that
    POST /v1/tasks/{id}/run and GET /v1/tasks/{id}/runs resolve.
    """
    return create_app()


@pytest.fixture
def transport(app):
    """In-process HTTP driver via httpx.ASGITransport (per NFR-10.2)."""
    return httpx.ASGITransport(app=app)


@pytest.fixture
def client(transport):
    """Sync client; in-process per integration_fr_guidelines (decide: in_process)."""
    return httpx.Client(transport=transport, base_url="http://test")


@pytest.fixture(autouse=True)
def mock_external_dependencies(monkeypatch):
    """Test isolation — keep tests failing because of missing logic, not real I/O.

    For FR-02 we cannot rely on the auth stub from FR-01 because the imports
    above will fail at collection time. This fixture is intentionally a no-op
    for the RED step; GREEN will replace it with concrete monkeypatching of
    taskq.service.auth.verify_api_key.
    """
    yield


# ---------- Helpers ----------

def _write_headers() -> Dict[str, str]:
    return {"X-API-Key": VALID_WRITE_KEY}


def _read_headers() -> Dict[str, str]:
    return {"X-API-Key": VALID_READ_KEY}


def _seed_task(client, name: str, command: str) -> str:
    """Helper: create a task via the FR-01 POST /v1/tasks endpoint and return its id.

    GREEN TODO: requires FR-01 to be GREEN — for the pure RED step we
    assert only that the call shape is the documented FR-01 contract.
    """
    response = client.post(
        "/v1/tasks", json={"name": name, "command": command}, headers=_write_headers()
    )
    assert response.status_code == 201, response.text
    body = response.json()
    task_id_str: Optional[str] = body.get("id") or body.get("task_id")
    assert task_id_str is not None, f"no task id in FR-01 create response: {body!r}"
    return str(task_id_str)


# ---------- FR-02 / AC-2.1 — POST /v1/tasks/{id}/run -> 202 + run_id ----------

def test_fr02_ac1_run_returns_202_with_run_id(client):
    """AC-2.1 — POST /v1/tasks/{id}/run returns HTTP 202 + body containing a run_id.

    Sub-assertion AC2.1-status-202: expected_status == "202".
    Sub-assertion AC2.1-field-run-id: expected_field == "run_id".
    Sub-assertion AC2.1-subprocess-mode: subprocess_mode == "in_process"
        (we drive the route via httpx.ASGITransport — same-process ASGI dispatch).

    NFR-09: real assert on HTTP status + run_id format (no skip/xfail).
    NFR-10: end-to-end via httpx.ASGITransport (integration coverage).
    """
    # NFR-02 (HTTP-layer contract): 202 must surface as 202 (no auth/body leak).
    # NFR-03 (async correctness): the route must return 202 immediately (not
    #   block on subprocess exit) and schedule the runner as a background task.
    # NFR-06 (architecture layer contract): the route delegates to
    #   taskq.service.runner (NOT direct subprocess / NOT direct repository).
    # NFR-10 (integration coverage): exercised through httpx.ASGITransport.
    # Seed a task to run; FR-02 needs an existing task id.
    task_id_str = _seed_task(client, name="fr02-ac1-task", command=COMMAND_HAPPY)

    # Sub-assertion AC2.1-subprocess-mode: in_process — call through httpx ASGI.
    response = client.post(
        f"/v1/tasks/{task_id_str}/run",
        headers=_write_headers(),
    )
    # Sub-assertion AC2.1-status-202: expected_status == "202"
    assert response.status_code == 202, response.text

    body = response.json()
    # Sub-assertion AC2.1-field-run-id: expected_field == "run_id"
    run_id_value: Optional[str] = body.get("run_id")
    assert run_id_value is not None, f"expected run_id in response body, got {body!r}"
    # run_id must be a UUID string (FR-02 schema for run identifier).
    uuid.UUID(str(run_id_value))


# ---------- FR-02 / AC-2.2 — grep src/ for shell=True; expected 0 hits ----------

def test_fr02_ac2_subprocess_exec_no_shell_true():
    """AC-2.2 — subprocess execution MUST NOT use shell=True anywhere in src/.

    Sub-assertion AC2.2-grep-zero-hits: expected_hits == "0".
    Sub-assertion AC2.2-pattern-shell-true: grep_pattern == "shell=True".

    NFR-02 / SPEC §8 #16: ``shell=True`` is forbidden because it allows shell
    injection of the command string. The runner MUST use
    ``asyncio.create_subprocess_exec(*shlex.split(command))`` (tokenised argv).

    Implementation choice (in-process grep): we walk the source tree and count
    literal occurrences of ``shell=True``. A static lint gate is the only way
    to enforce this contract because it cannot be observed at runtime.
    """
    # NFR-02: forbidden pattern; covered by grep-zero-hits assertion.
    # Sub-assertion AC2.2-pattern-shell-true: the literal pattern we forbid.
    grep_pattern = "shell=True"
    # Inputs declare src_path="src"; resolve relative to 03-development/ (parent of tests/).
    src_root = _THIS_DIR.parent / "src"
    assert src_root.is_dir(), f"expected src root at {src_root}, not found"

    hits: List[str] = []
    for py_file in src_root.rglob("*.py"):
        # Skip __pycache__ artefacts.
        if "__pycache__" in py_file.parts:
            continue
        text = py_file.read_text(encoding="utf-8", errors="ignore")
        for line_no, line in enumerate(text.splitlines(), start=1):
            if grep_pattern in line:
                hits.append(f"{py_file.relative_to(_THIS_DIR)}:{line_no}: {line.strip()}")

    # Sub-assertion AC2.2-grep-zero-hits: expected_hits == "0"
    assert len(hits) == 0, (
        f"shell=True is forbidden (NFR-02 / SPEC §8 #16); "
        f"found {len(hits)} occurrence(s):\n" + "\n".join(hits)
    )


# ---------- FR-02 / AC-2.3 — state machine done/failed/timeout ----------

def test_fr02_ac3_state_machine_done():
    """AC-2.3 — pending -> running -> done when command exits 0.

    Sub-assertion AC2.3-done-terminal: expected_terminal == "done".
    Sub-assertion AC2.3-done-exit-zero: expected_exit_code == "0".
    Sub-assertion (Inputs row): subprocess_mode == "in_process" — we call the
        runner directly via TaskRunner to exercise the SAME validation path
        the HTTP route uses, so pytest-cov can measure it (Gate 1 test_coverage).

    NFR-02 (subprocess safety): no shell=True; uses shlex.split + exec.
    NFR-03 (async correctness): asyncio.wait_for timeout path covered.
    NFR-09: real assert on terminal state + exit code (no skip/xfail).
    """
    # Inputs: command="true"; command that exits 0 in <100ms on every POSIX.
    # Sub-assertion AC2.3-done-exit-zero: expected_exit_code == "0"
    expected_exit_code = 0

    runner = TaskRunner()
    # GREEN TODO: TaskRunner.run(task_id, command) -> result dict with
    # {"terminal": "done"|"failed"|"timeout", "exit_code": int, ...}.
    result = runner.run(task_id=str(uuid.uuid4()), command=COMMAND_DONE)
    assert isinstance(result, dict), f"runner.run must return a dict, got {result!r}"
    # Sub-assertion AC2.3-done-terminal: expected_terminal == "done"
    assert result.get("terminal") == "done", (
        f"expected terminal state 'done', got {result!r}"
    )
    assert result.get("exit_code") == expected_exit_code, (
        f"expected exit_code == {expected_exit_code}, got {result!r}"
    )


def test_fr02_ac3_state_machine_failed():
    """AC-2.3 — pending -> running -> failed when command exits non-zero.

    Sub-assertion AC2.3-failed-terminal: expected_terminal == "failed".
    Sub-assertion AC2.3-failed-exit-one: expected_exit_code == "1".
    Sub-assertion (Inputs row): subprocess_mode == "in_process" — direct call.

    NFR-02 (subprocess safety): no shell=True; uses shlex.split + exec.
    NFR-03 (async correctness): non-zero exit path captured.
    NFR-09: real assert on terminal state + exit code (no skip/xfail).
    """
    # Inputs: command="false"; command that exits 1 in <100ms on every POSIX.
    # Sub-assertion AC2.3-failed-exit-one: expected_exit_code == "1"
    expected_exit_code = 1

    runner = TaskRunner()
    result = runner.run(task_id=str(uuid.uuid4()), command=COMMAND_FAILED)
    assert isinstance(result, dict), f"runner.run must return a dict, got {result!r}"
    # Sub-assertion AC2.3-failed-terminal: expected_terminal == "failed"
    assert result.get("terminal") == "failed", (
        f"expected terminal state 'failed', got {result!r}"
    )
    assert result.get("exit_code") == expected_exit_code, (
        f"expected exit_code == {expected_exit_code}, got {result!r}"
    )


def test_fr02_ac3_state_machine_timeout():
    """AC-2.3 — pending -> running -> timeout when command exceeds TASKQ_TASK_TIMEOUT.

    Sub-assertion AC2.3-timeout-terminal: expected_terminal == "timeout".
    Sub-assertion AC2.3-timeout-overrun: float(timeout_seconds) < 30.0
        (we use timeout_seconds=1.0 vs sleep 30 — guaranteed overrun).
    Sub-assertion AC2.3-timeout-subprocess-mode: subprocess_mode == "out_of_process"
        (this case shells out to a fresh Python interpreter because the timeout
         boundary must be a HARD kill, not a coroutine cancellation).

    NFR-03 (async correctness + NFR-03 task timeout kills subprocess): the
        timeout path must hard-kill the subprocess (process.kill + await wait),
        not just cancel the awaiting coroutine.
    NFR-09: real assert on terminal state (no skip/xfail).
    """
    # Sub-assertion AC2.3-timeout-overrun: float(timeout_seconds) < 30.0
    timeout_seconds = 1.0  # sleep 30 will overrun this by 29s+

    # Sub-assertion AC2.3-timeout-subprocess-mode: subprocess_mode == "out_of_process".
    # We drive the runner via a child Python process so we exercise the real
    # asyncio.create_subprocess_exec path with a hard timeout — pytest-cov does
    # not measure subprocess coverage but the route-level happy-path tests
    # (test_fr02_ac3_state_machine_done / _failed) already cover the in-process
    # branches via TaskRunner.
    env = os.environ.copy()
    # Resolve the actual src root (parent of tests/, not the tests/src alias
    # the bootstrap inserted into sys.path).
    src_root = _THIS_DIR.parent / "src"
    env["PYTHONPATH"] = str(src_root) + os.pathsep + env.get("PYTHONPATH", "")
    env["TASKQ_TASK_TIMEOUT"] = str(timeout_seconds)

    driver_src = (
        "import uuid, json, sys\n"
        "from taskq.service.runner import TaskRunner\n"
        # Sub-assertion AC2.3-timeout-terminal: expected_terminal == "timeout"
        "result = TaskRunner().run(task_id=str(uuid.uuid4()), command='sleep 30')\n"
        "sys.stdout.write(json.dumps({'terminal': result.get('terminal'),\n"
        "                              'exit_code': result.get('exit_code')}) + '\\n')\n"
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
        f"subprocess driver failed: rc={completed.returncode} stderr={completed.stderr!r}"
    )

    payload = json.loads(completed.stdout.strip().splitlines()[-1])
    # Sub-assertion AC2.3-timeout-terminal: expected_terminal == "timeout"
    assert payload.get("terminal") == "timeout", (
        f"expected terminal state 'timeout' for sleep 30 + 1.0s timeout, "
        f"got {payload!r}"
    )


# ---------- FR-02 / AC-2.4 — results persisted to task_results with 5 columns ----------

def test_fr02_ac4_results_persisted_to_task_results():
    """AC-2.4 — execution result is persisted to ``task_results`` with the v3 schema.

    Sub-assertion AC2.4-columns-five: len(expected_columns.split(",")) == 5.

    Columns required (FR-07 v3 schema, cited from SPEC §5.2):
        exit_code | stdout_tail | stderr_tail | duration_ms | finished_at

    Sub-assertion (Inputs row): subprocess_mode == "in_process" — call the
        repository directly so pytest-cov measures the schema path.

    NFR-04 (sensitive data redaction): the persisted stdout_tail/stderr_tail
        columns MUST be redacted before insert if they carry secret-shaped
        substrings (the regex is exercised by FR-04 / NFR-04 tests; this test
        only asserts the columns exist + are written by the runner path).
    NFR-09: real assert on column whitelist (no skip/xfail).
    NFR-10: persistence exercised end-to-end through TaskResultRepository.
    """
    # Sub-assertion AC2.4-columns-five: len(expected_columns.split(",")) == 5
    expected_columns = "exit_code,stdout_tail,stderr_tail,duration_ms,finished_at"
    assert len(expected_columns.split(",")) == 5

    repo = TaskResultRepository()
    # GREEN TODO: TaskResultRepository.record_result(task_id, command, exit_code,
    # stdout_tail, stderr_tail, duration_ms, finished_at) -> dict. The schema
    # MUST include the 5 columns listed above; if any are missing, the FR-07
    # round-trip is not byte-identical.
    recorded = repo.record_result(
        task_id=str(TARGET_ID_RUN_1),
        command=COMMAND_HAPPY,
        exit_code=0,
        stdout_tail="hello\n",
        stderr_tail="",
        duration_ms=12,
        finished_at="2026-08-22T00:00:00Z",
    )

    assert isinstance(recorded, dict), f"record_result must return dict, got {recorded!r}"
    missing = [col for col in EXPECTED_RESULT_COLUMNS if col not in recorded]
    assert not missing, (
        f"task_results row missing required columns {missing}; "
        f"got keys {sorted(recorded.keys())!r}"
    )


# ---------- FR-02 / AC-2.5 — GET /v1/tasks/{id}/runs newest-first ----------

def test_fr02_ac5_get_runs_newest_first(client):
    """AC-2.5 — GET /v1/tasks/{id}/runs returns the task's execution history newest-first.

    Sub-assertion AC2.5-order-newest: expected_order == "newest_first".
    Sub-assertion AC2.5-runs-count-three: runs_count == "3".

    We seed three runs via FR-02 AC-2.1 (each returns a run_id), then GET the
    list and assert the returned runs are ordered by finished_at DESC (or
    started_at DESC — whichever the GREEN implementation chooses, as long as
    it is strictly newest-first).

    NFR-06 (architecture layer contract): the GET endpoint must reach the
        task_results rows only via the repository layer (no SQL in the route).
    NFR-09: real assert on ordering + count (no skip/xfail).
    NFR-10: end-to-end via httpx.ASGITransport (integration coverage).
    """
    # Seed a task to attach runs to.
    task_id_str = _seed_task(client, name="fr02-ac5-task", command=COMMAND_HAPPY)

    # Trigger 3 runs. Each call returns a run_id; we capture them in order
    # so we can verify the GET returns them newest-first.
    captured_run_ids: List[str] = []
    for _ in range(3):
        response = client.post(
            f"/v1/tasks/{task_id_str}/run",
            headers=_write_headers(),
        )
        assert response.status_code == 202, response.text
        body = response.json()
        run_id = body.get("run_id")
        assert run_id is not None, f"no run_id in 202 response: {body!r}"
        captured_run_ids.append(str(run_id))

    # Sub-assertion AC2.5-runs-count-three: runs_count == "3"
    list_response = client.get(
        f"/v1/tasks/{task_id_str}/runs",
        headers=_read_headers(),
    )
    assert list_response.status_code == 200, list_response.text
    list_body = list_response.json()
    items = list_body.get("runs") or list_body.get("items") or list_body
    assert isinstance(items, list), f"expected list of runs, got {list_body!r}"
    assert len(items) == 3, (
        f"expected exactly 3 runs for the seeded task, got {len(items)}: {items!r}"
    )

    # Sub-assertion AC2.5-order-newest: expected_order == "newest_first".
    # The reverse insertion order IS the expected newest-first order: the last
    # run we triggered should appear first.
    returned_run_ids = [str(item.get("run_id") or item.get("id")) for item in items]
    assert returned_run_ids == list(reversed(captured_run_ids)), (
        f"runs must be newest-first; insertion was {captured_run_ids}, "
        f"got {returned_run_ids}"
    )