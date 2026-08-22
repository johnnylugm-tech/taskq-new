"""RED tests for FR-01: Task resource CRUD API.

Test names MUST match TEST_SPEC.md (`02-architecture/TEST_SPEC.md`).
These tests intentionally fail at collection time because the source
modules are not implemented yet — that is the valid RED state for
TDD-RED. Do NOT add try/except ImportError wrappers.

GREEN TODO summary (declarations per SAB.json fr_module_traceability.FR-01):
  - taskq.api.app        -> create_app() returning a FastAPI instance
  - taskq.api.routes.tasks -> router with POST/GET/GET-list/DELETE on /v1/tasks
  - taskq.service.tasks  -> service layer (create/get/list/delete_task)
  - taskq.repository.tasks -> DB-backed persistence (with name uniqueness)
  - taskq.api.schemas    -> TaskCreate / TaskRead pydantic models
"""

import json
import uuid
from typing import Any, Dict, List, Optional

import httpx
import pytest

# GREEN TODO: taskq.api.app must export create_app() returning a FastAPI instance.
# GREEN TODO: taskq.api.routes.tasks must define router with 4 endpoints under /v1/tasks:
#     - POST   ""       (scope=write)  -> TaskService.create_task
#     - GET    "/{id}"  (scope=read)   -> TaskService.get_task
#     - GET    ""       (scope=read)   -> TaskService.list_tasks(cursor pagination)
#     - DELETE "/{id}"  (scope=admin)  -> TaskService.delete_task (cascades results)
from taskq.api.app import create_app  # noqa: E402

# GREEN TODO: taskq.api.schemas must export TaskCreate (with name + command + max 1000 chars
# validation + injection-blacklist) and TaskRead (id + name + status + created_at + ...).
from taskq.api.schemas import TaskCreate  # noqa: E402

# GREEN TODO: taskq.service.tasks must expose the business logic the routes will call.
from taskq.service.tasks import TaskService  # noqa: E402

# GREEN TODO: taskq.repository.tasks must provide task persistence (incl. name uniqueness check).
from taskq.repository.tasks import TaskRepository  # noqa: E402


# ---------- Constants declared by TEST_SPEC Inputs rows ----------

VALID_WRITE_KEY = "taskq-write-test-key-abc123"
VALID_READ_KEY = "taskq-read-test-key-abc456"
VALID_ADMIN_KEY = "taskq-admin-test-key-xyz789"

NAME_HAPPY = "build-job-001"
COMMAND_HAPPY = "echo hello"
EXISTING_NAME = "dup-name"
NEW_NAME = "dup-name"
TARGET_ID_KNOWN = uuid.UUID("11111111-1111-1111-1111-111111111111")
TARGET_ID_MISSING = uuid.UUID("22222222-2222-2222-2222-222222222222")
TARGET_ID_UNKNOWN = "uuid-X"
TARGET_ID_WITH_RUNS = uuid.UUID("33333333-3333-3333-3333-333333333333")


# ---------- Fixtures ----------

@pytest.fixture
def app():
    """Fresh FastAPI app per test (function-scoped)."""
    application = create_app()
    return application


@pytest.fixture
def transport(app):
    """In-process HTTP driver via httpx.ASGITransport (per NFR-10.2)."""
    return httpx.ASGITransport(app=app)


@pytest.fixture
def client(transport):
    """Sync client; in-process per integration_fr_guidelines (decide: in_process)."""
    return httpx.Client(transport=transport, base_url="http://test")


@pytest.fixture(autouse=True)
def mock_external_dependencies(monkeypatch, request):
    """Test isolation — keep tests failing because of missing logic, not real I/O.

    GREEN TODO: when implementing `taskq.service.auth.verify_api_key`,
    replace `real_verify` here with a stub that returns the requested scope
    by key instead of hitting the DB / hashing logic.
    """
    # GREEN TODO: AuthService must accept key, return {"scope": ...} OR raise.
    def _stub_verify(key: str, scope_required: Optional[str] = None) -> Dict[str, str]:
        mapping = {
            VALID_WRITE_KEY: "write",
            VALID_READ_KEY: "read",
            VALID_ADMIN_KEY: "admin",
        }
        if key not in mapping:
            from taskq.service.auth import InvalidAPIKey  # GREEN TODO: define this error
            raise InvalidAPIKey("invalid key")
        return {"scope": mapping[key], "key_id": key}

    # GREEN TODO: monkeypatch.setattr("taskq.service.auth.verify_api_key", _stub_verify)
    # is added by GREEN once verify_api_key exists at that import path.
    yield


# ---------- Helpers ----------

def _payload(name: str = NAME_HAPPY, command: str = COMMAND_HAPPY) -> Dict[str, Any]:
    return {"name": name, "command": command}


def _write_headers() -> Dict[str, str]:
    return {"X-API-Key": VALID_WRITE_KEY}


def _read_headers() -> Dict[str, str]:
    return {"X-API-Key": VALID_READ_KEY}


def _admin_headers() -> Dict[str, str]:
    return {"X-API-Key": VALID_ADMIN_KEY}


def _assert_problem_json(response: httpx.Response) -> None:
    assert response.headers.get("content-type", "").startswith("application/problem+json"), (
        f"expected application/problem+json, got {response.headers.get('content-type')!r}"
    )


# ---------- FR-01 / AC-1.1 — POST valid body -> 201 with UUID id ----------

def test_fr01_ac1_post_creates_task_201(client):
    """AC-1.1 — POST /v1/tasks with valid write key + valid body returns 201 + task id (UUID).

    NFR-09: zero-skip / every test asserts (assert status_code + UUID format).
    NFR-10: end-to-end via httpx.ASGITransport (integration coverage).
    """
    # Sub-assertion AC1.1-status-201: expected_status == "201"
    response = client.post("/v1/tasks", json=_payload(), headers=_write_headers())
    assert response.status_code == 201, response.text
    body = response.json()
    # GREEN TODO: response body must include a task id under "id" or "task_id"
    task_id_value: Optional[str] = body.get("id") or body.get("task_id")
    assert task_id_value is not None, f"no task id in response body: {body!r}"
    # Sub-assertion AC1.1-id-format: expected_id_format == "uuid"
    uuid.UUID(str(task_id_value))


# ---------- FR-01 / AC-1.2 — POST without X-API-Key -> 401 + problem+json ----------

def test_fr01_ac2_post_no_api_key_returns_401(client):
    """AC-1.2 — POST /v1/tasks without X-API-Key returns 401 + application/problem+json.

    FR-10: non-2xx responses are application/problem+json (RFC 7807 contract).
    NFR-02: security boundary — missing/invalid key must return problem+json, not stack/SQL/path leak.
    """
    # Sub-assertion AC1.2-status-401: expected_status == "401"
    response = client.post("/v1/tasks", json=_payload())
    assert response.status_code == 401, response.text
    # Sub-assertion AC1.2-content-type: expected_content_type == "application/problem+json"
    _assert_problem_json(response)


# ---------- FR-01 / AC-1.3 — POST invalid body -> 422 + problem+json ----------

def test_fr01_ac3_post_invalid_body_returns_422(client):
    """AC-1.3 — POST /v1/tasks with body violating validation rules returns 422 + problem+json.

    Three validation rules per SPEC §7:
      - non-empty (name required, command required)
      - <=1000 chars (combined body length OR single-field length)
      - injection-blacklist characters

    Sub-assertion AC1.3-body-len-over-1000: body_exceeds_max_len == "true".
    We send a payload whose string field exceeds the 1000-char limit.

    FR-10: 422 must surface as application/problem+json.
    NFR-02: input validation guardrail (rejects oversize/injection-char bodies).
    NFR-05: docstring carries [FR-01] / [NFR-XX] citation requirement (this comment).
    """
    # Sub-assertion AC1.3-status-422: expected_status == "422"
    oversize_command = "x" * 1001  # > 1000 chars
    response = client.post(
        "/v1/tasks",
        json={"name": NAME_HAPPY, "command": oversize_command},
        headers=_write_headers(),
    )
    assert response.status_code == 422, response.text
    _assert_problem_json(response)


# ---------- FR-01 / AC-1.4 — POST duplicate name -> 409 + problem+json ----------

def test_fr01_ac4_post_duplicate_name_returns_409(client):
    """AC-1.4 — POST /v1/tasks with a name that already exists returns 409 + problem+json.

    Sub-assertion AC1.4-name-collision: existing_name == new_name.
    The first POST inserts the name; the second POST with the same name must 409.

    FR-10: 409 must be application/problem+json.
    NFR-02: uniqueness guard at persistence layer (no string-concat SQL).
    """
    # First POST creates the task with EXISTING_NAME.
    first_response = client.post(
        "/v1/tasks",
        json={"name": EXISTING_NAME, "command": COMMAND_HAPPY},
        headers=_write_headers(),
    )
    assert first_response.status_code == 201, first_response.text

    # Second POST with the same name must collide at the DB layer.
    # Sub-assertion AC1.4-status-409: expected_status == "409"
    second_response = client.post(
        "/v1/tasks",
        json={"name": NEW_NAME, "command": COMMAND_HAPPY},
        headers=_write_headers(),
    )
    assert second_response.status_code == 409, second_response.text
    _assert_problem_json(second_response)


# ---------- FR-01 / AC-1.5 — GET existing -> 200 ----------

def test_fr01_ac5_get_existing_returns_200(client):
    """AC-1.5 — GET /v1/tasks/{id} for an existing id returns 200 + full task fields.

    NFR-01: single-get target — p95 < 30ms @ 10k rows (pytest-benchmark cross-checks).
    NFR-10: end-to-end via httpx.ASGITransport (integration coverage).
    """
    # Create a task first so we have a known id.
    create_response = client.post(
        "/v1/tasks", json=_payload(), headers=_write_headers()
    )
    assert create_response.status_code == 201, create_response.text
    created_body = create_response.json()
    task_id_str: Optional[str] = created_body.get("id") or created_body.get("task_id")
    assert task_id_str is not None

    # Sub-assertion AC1.5-status-200: expected_status == "200"
    response = client.get(f"/v1/tasks/{task_id_str}", headers=_read_headers())
    assert response.status_code == 200, response.text
    body = response.json()
    # GREEN TODO: full task fields (id, name, command, status, created_at, ...)
    assert "name" in body
    assert body["name"] == NAME_HAPPY


# ---------- FR-01 / AC-1.6 — GET unknown -> 404 + problem+json ----------

def test_fr01_ac6_get_unknown_returns_404(client):
    """AC-1.6 — GET /v1/tasks/{unknown} returns 404 + application/problem+json.

    FR-10: 404 must be application/problem+json.
    NFR-02: error body must not leak stack/SQL/path/schema (FR-10 whitelist).
    """
    # Sub-assertion AC1.6-status-404: expected_status == "404"
    response = client.get(f"/v1/tasks/{TARGET_ID_MISSING}", headers=_read_headers())
    assert response.status_code == 404, response.text
    _assert_problem_json(response)


# ---------- FR-01 / AC-1.7 — GET list supports cursor pagination ----------

def test_fr01_ac7_list_supports_cursor_pagination(client):
    """AC-1.7 — GET /v1/tasks supports ?status= ?limit= ?cursor=; cursor-based (NOT offset).

    Sub-assertion AC1.7-cursor-not-offset: pagination_mode == "cursor".
    Sub-assertion AC1.7-default-limit-50: limit == "50".
    The response must contain a next_cursor token and must NOT contain an offset field.

    NFR-01: cursor pagination avoids offset-scan N+1 (constant SQL count ≤ 4 across {1,100,1000,10000}).
    NFR-10: list endpoint is exercised via httpx.ASGITransport (integration coverage).
    """
    response = client.get(
        "/v1/tasks",
        params={"limit": 50, "cursor": "opaque-token"},
        headers=_read_headers(),
    )
    assert response.status_code == 200, response.text
    body = response.json()
    # cursor-based pagination is required — 'next_cursor' field signals cursor mode
    assert "next_cursor" in body or "next_page_token" in body or "cursor" in body, (
        f"expected cursor-paginated body, got {body!r}"
    )
    # must NOT be offset-based
    body_text = json.dumps(body)
    assert "offset" not in body_text, (
        f"offset-based pagination is forbidden (SPEC §3 FR-01): {body_text!r}"
    )


# ---------- FR-01 / AC-1.8 — GET list ?limit=201 -> 422 + problem+json ----------

def test_fr01_ac8_list_limit_over_200_returns_422(client):
    """AC-1.8 — GET /v1/tasks?limit=201 (limit > 200) returns 422 + problem+json.

    Sub-assertion AC1.8-limit-over-200: limit_over_200 == "true".

    FR-10: 422 must be application/problem+json.
    NFR-02: input validation guardrail (limit cap protects against unbounded scan).
    NFR-09: zero-skip (this test asserts, never pytest.skip).
    """
    response = client.get(
        "/v1/tasks",
        params={"limit": 201},
        headers=_read_headers(),
    )
    assert response.status_code == 422, response.text
    _assert_problem_json(response)


# ---------- FR-01 / AC-1.9 — DELETE with write scope -> 403 generic, NO LEAK ----------

def test_fr01_ac9_delete_write_scope_returns_403_no_leak(client):
    """AC-1.9 — DELETE /v1/tasks/{id} with write scope returns 403, body MUST NOT leak existence.

    Sub-assertion AC1.9-status-403: expected_status == "403".
    Sub-assertion AC1.9-body-no-leak: response_body == "generic_403".

    The body must be a generic 403 problem+json that does NOT contain the
    task id (since the id may or may not exist — write-scope must not reveal which).

    FR-10: 403 must be application/problem+json.
    NFR-02: 403 body must not leak resource existence (SPEC §8 #6 — adversarial risk R4).
    """
    # Try to delete a potentially-non-existent id with a write (non-admin) key.
    response = client.delete(
        f"/v1/tasks/{TARGET_ID_UNKNOWN}",
        headers=_write_headers(),
    )
    assert response.status_code == 403, response.text
    _assert_problem_json(response)
    # GREEN TODO: body must be a generic 403 problem document; must not include
    # the task id, must not say "not found", must not say "forbidden because missing".
    body_text = response.text
    assert TARGET_ID_UNKNOWN not in body_text, (
        f"403 body must not leak target id (NP-02 / SPEC §8 #6): {body_text!r}"
    )
    forbidden_leak_tokens = ("not found", "does not exist", "missing", "no such task")
    for token in forbidden_leak_tokens:
        assert token not in body_text.lower(), (
            f"403 body must not leak existence info (got {token!r} in {body_text!r})"
        )


# ---------- FR-01 / AC-1.10 — DELETE with admin scope cascades results ----------

def test_fr01_ac10_delete_admin_cascades_results(client):
    """AC-1.10 — DELETE /v1/tasks/{id} with admin-scope removes task + result rows in same tx.

    Sub-assertion AC1.10-results-rows-zero: task_results_rows_before == "5".
    Sub-assertion AC1.10-state-mode-shared: state_mode == "shared".

    We seed 5 task_results rows for a task, then DELETE with admin scope, and
    assert the service delete function is invoked once on a single transaction
    boundary (cascade). This is the in-process coverage path required for
    Gate 1 — pytest-cov cannot measure code running inside the subprocess.

    FR-06: one Session per request, context-managed commit/rollback; cascade in same transaction.
    NFR-06: api > service > repository > models layers contract (delete_task delegates to repository).
    NFR-10: end-to-end via httpx.ASGITransport (integration coverage).
    """
    # Seed via the API: create the parent task, then post 5 runs.
    create_response = client.post(
        "/v1/tasks", json=_payload(), headers=_write_headers()
    )
    assert create_response.status_code == 201, create_response.text
    body = create_response.json()
    task_id_str: Optional[str] = body.get("id") or body.get("task_id")
    assert task_id_str is not None
    task_id_uuid = uuid.UUID(str(task_id_str))

    # GREEN TODO: the persistence layer must populate task_results; here we
    # seed by invoking TaskService.create_task_result five times.
    service = TaskService()  # GREEN TODO: this must be wired up at app startup
    # AC1.10-results-rows-zero: seed exactly 5 task_results rows
    for _ in range(5):
        service.create_task_result(task_id=task_id_uuid, command=COMMAND_HAPPY)

    # Now DELETE with admin key.
    response = client.delete(
        f"/v1/tasks/{task_id_str}",
        headers=_admin_headers(),
    )
    assert response.status_code in (200, 204), response.text

    # GREEN TODO: at this point both the task row AND its 5 task_results rows
    # must be gone — deleted in a single transaction. We assert via the service:
    results_remaining: List[Any] = service.list_results_for_task(task_id=task_id_uuid)
    assert results_remaining == [], (
        f"admin delete did not cascade results; {len(results_remaining)} remain"
    )
    with pytest.raises(Exception):
        # GREEN TODO: TaskService.get_task must raise on unknown id (TaskNotFound).
        service.get_task(task_id=task_id_uuid)


# ---------- In-process coverage helpers (Gate 1 test_coverage) ----------
#
# pytest-cov cannot measure subprocess coverage; these tests import the handler
# / service / repository modules directly to exercise the SAME validation paths
# as the HTTP tests above. GREEN must satisfy them in addition to the HTTP
# tests. They are kept in the same file per the integration_fr_guidelines
# "both test types coexist" rule.

def _build_task_create_kwargs() -> Dict[str, Any]:
    return {"name": NAME_HAPPY, "command": COMMAND_HAPPY}


def test_fr01_inprocess_create_via_service_returns_uuid_and_persists():
    """In-process mirror of AC-1.1: TaskService.create_task returns a UUID.

    NFR-06: layer contract — service is the boundary business logic; no sqlalchemy leak.
    NFR-09: real assert on UUID format and persistence.
    """
    service = TaskService()
    created = service.create_task(**_build_task_create_kwargs())
    task_id = getattr(created, "id", None) or (created or {}).get("id")
    assert task_id is not None
    uuid.UUID(str(task_id))


def test_fr01_inprocess_duplicate_name_raises_409_signal():
    """In-process mirror of AC-1.4: TaskService.create_task on duplicate name
    raises a domain error that the API layer must translate to HTTP 409."""
    service = TaskService()
    service.create_task(name=EXISTING_NAME, command=COMMAND_HAPPY)
    with pytest.raises(Exception) as excinfo:
        service.create_task(name=NEW_NAME, command=COMMAND_HAPPY)
    # GREEN TODO: a dedicated DuplicateTaskName error class must exist; the
    # route layer catches it and maps to HTTP 409 + problem+json.
    assert excinfo.value.__class__.__name__ in {
        "DuplicateTaskName",
        "TaskNameConflict",
        "UniqueViolation",
    }


def test_fr01_inprocess_validation_oversize_command_raises():
    """In-process mirror of AC-1.3: TaskCreate rejects body > 1000 chars."""
    # GREEN TODO: TaskCreate is a pydantic model with max_length=1000 on `command`.
    with pytest.raises(Exception):
        TaskCreate(name=NAME_HAPPY, command="x" * 1001)


def test_fr01_inprocess_list_limit_over_200_raises():
    """In-process mirror of AC-1.8: list endpoint rejects limit > 200."""
    service = TaskService()
    with pytest.raises(Exception):
        service.list_tasks(limit=201, cursor=None)


def test_fr01_inprocess_repository_name_uniqueness_enforced():
    """Repository layer enforces uniqueness — second insert with same name raises.

    NFR-02: uniqueness enforced at persistence layer (no string-concat SQL).
    NFR-06: repository is the only layer that touches sqlalchemy — leakage check.
    """
    repo = TaskRepository()
    first = repo.create(name=EXISTING_NAME, command=COMMAND_HAPPY)
    assert first is not None
    with pytest.raises(Exception) as excinfo:
        repo.create(name=EXISTING_NAME, command=COMMAND_HAPPY)
    # GREEN TODO: SQLAlchemy IntegrityError or a domain wrapper.
    assert excinfo.value.__class__.__name__ in {
        "IntegrityError",
        "UniqueViolation",
        "DuplicateTaskName",
    }


def test_fr01_inprocess_delete_admin_calls_cascade_in_one_transaction(monkeypatch):
    """AC-1.10 in-process: TaskService.delete_task must call both
    `tasks` deletion and `task_results` deletion on a single transaction.

    FR-06: transaction boundary — cascade must happen in ONE unit_of_work (commit or rollback).
    NFR-06: layer contract — service orchestrates, repository executes; business holds no Session.
    NFR-09: real assertion (counts both repo calls); no skip/xfail.
    """
    service = TaskService()
    seen: Dict[str, int] = {"delete_task_calls": 0, "delete_results_calls": 0}

    def _fake_delete_task(task_id):  # noqa: ANN001
        seen["delete_task_calls"] += 1

    def _fake_delete_results(task_id):  # noqa: ANN001
        seen["delete_results_calls"] += 1
        return 5  # 5 rows removed

    # GREEN TODO: TaskService.delete_task must internally call both
    # repository delete-task AND delete-results within one transactional
    # boundary. Patch the two repository methods so we can count calls
    # without needing real DB access.
    monkeypatch.setattr(service, "delete_task_row", _fake_delete_task, raising=False)
    monkeypatch.setattr(service, "delete_results_for_task", _fake_delete_results, raising=False)

    service.delete_task(task_id=uuid.uuid4())

    assert seen["delete_task_calls"] == 1, "task row must be deleted exactly once"
    assert seen["delete_results_calls"] == 1, (
        "result rows must be deleted (cascade) in the same transaction"
    )


# ---------- FR-01 coverage-padding tests (Gate 1 test_coverage → 100%) ----------
#
# These are NOT new ACs — they exercise branches of FR-01-owned code paths that
# the HTTP/in-process AC tests above don't reach. Each test names the source
# lines it covers in a comment so future readers can delete tests surgically
# if branches become unreachable.


def test_fr01_inprocess_list_limit_zero_raises():
    """TaskService.list_tasks(limit=0) raises ValueError.

    Covers src/taskq/service/tasks.py:50 (limit<1 input guard).
    NFR-09: real assert (no skip).
    """
    service = TaskService()
    with pytest.raises(ValueError):
        service.list_tasks(limit=0, cursor=None)


def test_fr01_inprocess_repository_list_limit_clamped_and_limit_over_200_raises():
    """TaskRepository.list guards against limit<1 (clamp) and limit>200 (raise).

    Covers src/taskq/repository/tasks.py:160 (limit<1 clamp),
    and src/taskq/repository/tasks.py:162 (limit>200 raise) — these are the
    defense-in-depth checks that the service layer ALSO enforces, so they
    must be reachable directly to satisfy the 100% line-coverage gate.
    NFR-09: real asserts (no skip).
    """
    repo = TaskRepository()
    # Create at least one row so the clamp path returns a non-empty page.
    repo.create(name="clamp-target-a", command=COMMAND_HAPPY)
    page, _ = repo.list(limit=0, cursor=None, status=None)
    assert len(page) <= 1, "limit=0 must clamp to 1"
    with pytest.raises(ValueError):
        repo.list(limit=201, cursor=None, status=None)


def test_fr01_inprocess_list_filter_by_queued_status_returns_only_queued_rows():
    """TaskService.list_tasks(status='queued') must return rows where status='queued'.

    Covers src/taskq/repository/tasks.py:168 (status WHERE clause). Status
    defaults to 'queued' at insert time (see taskq.models.task.Task.status);
    we use a unique name and assert it appears in the filtered set while
    an unrelated name (created under status='queued' too, but filtered by
    status='never-existed') is absent — using a non-existent status proves
    the WHERE clause ran (otherwise the unrelated row would show up).
    NFR-09: real assert on filtered set (no skip).
    """
    service = TaskService()
    service.create_task(name="status-queued-marker-1", command=COMMAND_HAPPY)
    out_queued = service.list_tasks(limit=200, cursor=None, status="queued")
    names_queued = {item.get("name") for item in out_queued.get("items", [])}
    assert "status-queued-marker-1" in names_queued
    out_unknown = service.list_tasks(limit=200, cursor=None, status="this-status-does-not-exist")
    assert len(out_unknown.get("items", [])) == 0


def test_fr01_inprocess_list_with_cursor_decodes_token_and_resumes():
    """list_tasks(cursor=<opaque>) decodes the cursor token and resumes after it.

    Covers src/taskq/repository/tasks.py:172-176 (cursor decoded branch).
    NFR-09: real assert on cursor pagination contract (no skip).
    """
    service = TaskService()
    # Seed a few rows so a single-page result has has_more=True.
    for i in range(3):
        service.create_task(name=f"cursor-pickup-{i}", command=COMMAND_HAPPY)
    page1 = service.list_tasks(limit=2, cursor=None, status=None)
    next_cursor = page1.get("next_cursor")
    assert isinstance(next_cursor, str) and next_cursor, (
        f"expected an opaque next_cursor, got {page1!r}"
    )
    assert len(page1.get("items", [])) == 2
    # Decode + resume: passing the opaque cursor back must succeed and return
    # an items list (possibly empty when the store is small). The mere fact
    # that the call does not raise and that the cursor token survived proves
    # the decoded branch in repository.list was exercised.
    page2 = service.list_tasks(limit=10, cursor=next_cursor, status=None)
    assert "items" in page2
    page1_ids = {row.get("id") for row in page1.get("items", [])}
    page2_ids = {row.get("id") for row in page2.get("items", [])}
    # Decoded cursor resumes strictly AFTER the cursor position, so page2
    # must not re-return the pinned row from page1.
    assert page1_ids.isdisjoint(page2_ids) or len(page2_ids) == 0


def test_fr01_inprocess_list_has_more_emits_next_cursor_token():
    """List with limit=1 over ≥2 rows emits a non-empty next_cursor.

    Covers src/taskq/repository/tasks.py:188-189 (next_cursor encode call),
    which in turn exercises src/taskq/repository/tasks.py:91-92 (_encode_cursor body).
    NFR-09: real assert on cursor presence + format (no skip).
    """
    service = TaskService()
    service.create_task(name="hasmore-a", command=COMMAND_HAPPY)
    service.create_task(name="hasmore-b", command=COMMAND_HAPPY)
    page = service.list_tasks(limit=1, cursor=None, status=None)
    next_cursor = page.get("next_cursor")
    assert isinstance(next_cursor, str) and len(next_cursor) > 0


def test_fr01_inprocess_taskcreate_blacklist_char_raises_value_error():
    """TaskCreate rejects command/name with characters in the injection blacklist.

    Covers src/taskq/api/schemas.py:30 (blacklist validator raise).
    NFR-09: real assert on ValueError raised by the validator (no skip).
    """
    with pytest.raises(ValueError):
        TaskCreate(name="ok-name", command="echo a; rm -rf /")

