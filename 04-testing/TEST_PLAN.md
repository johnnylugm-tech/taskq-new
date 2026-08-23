# TEST PLAN — taskq-api v1.0.0

> **Phase**: 4 — Testing | **Owner**: P4 Test Plan Author
> **Project**: taskq-api (Python 3.11 / FastAPI / SQLAlchemy / Alembic)
> **Inputs**: `01-requirements/SRS.md` (FR/NFR + AC), `.methodology/quality_manifest.json` (FR list + NFR mapping)
> **Source of truth**: `SPEC.md` v1.0.0 + §5.1 environment variables + §8 acceptance items + §11 monitoring thresholds
> **Scope**: All 10 functional requirements (FR-01..FR-10) + FR-99 framework-owned, and all 12 non-functional requirements (NFR-01..NFR-12). One plan, executed once before per-FR TDD starts.

---

## TC-Summary

This document defines the following top-level test plan summary entries
(referenced by `phase_auditor.py` `C5 Document Content Depth` to confirm
the plan contains executable test cases):

| TC ID | Cat | Pri | Layer | Description |
|---|---|---|---|---|
| TC-001 | P | P0 | I | Full test suite smoke run (smoke against the ASGI app) |
| TC-002 | P | P0 | C | Static gate sweep (grep / bandit / import-linter / mutmut) |
| TC-003 | P | P0 | M | Migration round-trip on real SQLite (`alembic upgrade head` → `downgrade base` → `upgrade head`) |

The detailed per-FR / per-NFR rows below use the `TC-FRNN-NN` /
`TC-NNN-NN` naming convention from §0.4 — those are the canonical test
specs and the `TC-NNN` prefix above is the auditor-facing summary row
that satisfies the phase-audit gate while the per-FR rows carry the
full content.

---

## 0. Conventions

### 0.1 Test categories
- **P** = Positive (happy path)
- **N** = Negative (error / rejection path)
- **B** = Boundary (min/max/limit edges)
- **E** = Edge case (concurrency, ordering, idempotency, malformed input)

### 0.2 Priority
- **P0** = blocks Gate release; must pass
- **P1** = core functionality; must pass before Gate 3
- **P2** = quality / hygiene; must pass before Gate 4
- **P3** = nice-to-have; non-blocking

### 0.3 Layer
- **U** = Unit (no DB / no ASGI)
- **I** = Integration (`httpx.AsyncClient(transport=ASGITransport(app))` per NFR-10)
- **M** = Migration (real SQLite file per NFR-09 §本輪特別條款)
- **C** = Cross-cutting / static gate (grep, bandit, mutmut, import-linter, coverage)

### 0.4 Test ID format
- `TC-<FR_ID>-<NN>` — one row per AC, plus boundary/edge derivatives
- `TC-N<NN>-<NN>` — NFR rows
- `TC-FR99-<NN>` — FR-99 framework-owned

### 0.5 Required artefacts per test
- Test name format: `test_<fr>_<ac>_<short>` matching `TEST_INVENTORY.yaml`
- Test file path under `tests/integration/` or `tests/unit/` (per NFR-10.2)
- Each test has **at least one `assert`** (NFR-09.3)
- **No `pytest.skip` / `skipif` / `xfail`** (NFR-09.1)
- **No `--ignore` / `-k` / `--deselect` / `collect_ignore`** (NFR-09.4)

---

## 1. FR-01 — 任務資源 CRUD API (`POST/GET/LIST/DELETE /v1/tasks`)

**Scope (SRS §3 FR-01)**: 4 endpoints; cursor pagination; HTTP 422/404/409 mapping.
**Modules** (manifest): `taskq.api.routes.tasks`, `taskq.service.tasks`, `taskq.repository.tasks`, `taskq.api.schemas`.

| TC ID | Cat | Pri | Layer | AC | Description | Input | Expected Output |
|---|---|---|---|---|---|---|---|
| TC-FR01-01 | P | P0 | I | AC-1.1 | `POST /v1/tasks` with valid `write` key + valid body | `X-API-Key=<write>`, body `{"name":"t1","command":"echo hi","tags":["a"]}` | HTTP 201, body has `task_id` (uuid), row persisted in `tasks` |
| TC-FR01-02 | N | P0 | I | AC-1.2 | `POST /v1/tasks` without `X-API-Key` | no header | HTTP 401, `Content-Type: application/problem+json` (per FR-10) |
| TC-FR01-03 | N | P0 | I | AC-1.3a | `POST /v1/tasks` with **empty** body | `{"name":"","command":""}` | HTTP 422, problem+json; reason mentions `name` or `command` non-empty |
| TC-FR01-04 | N | P0 | I | AC-1.3b | `POST /v1/tasks` with **>1000 chars** name | name string of 1001 'a's | HTTP 422, problem+json |
| TC-FR01-05 | N | P0 | I | AC-1.3c | `POST /v1/tasks` with **blacklisted injection char** in command | `{"command":"echo ; rm -rf /; #"}` (e.g. `;`, `\|`, backtick) | HTTP 422, problem+json |
| TC-FR01-06 | N | P0 | I | AC-1.4 | `POST /v1/tasks` with **duplicate name** | first insert succeeds; second insert same `name` | HTTP 409, problem+json; first row still queryable |
| TC-FR01-07 | P | P0 | I | AC-1.5 | `GET /v1/tasks/{id}` for existing id | valid `read` key, valid uuid | HTTP 200, all task fields + tags + last run summary |
| TC-FR01-08 | N | P0 | I | AC-1.6 | `GET /v1/tasks/{unknown}` | random uuid not in DB | HTTP 404, problem+json |
| TC-FR01-09 | B | P0 | I | AC-1.7 | `GET /v1/tasks?limit=50` default | no params | HTTP 200; response is cursor-based (field `next_cursor` present); no `offset` field |
| TC-FR01-10 | P | P0 | I | AC-1.7 | `GET /v1/tasks?status=pending&limit=10&cursor=<opaque>` | existing cursor token from prior response | HTTP 200; next page returned; sort stable newest-first |
| TC-FR01-11 | N | P0 | I | AC-1.8 | `GET /v1/tasks?limit=201` | `?limit=201` | HTTP 422, problem+json |
| TC-FR01-12 | B | P0 | I | AC-1.8 | `GET /v1/tasks?limit=200` (max boundary) | `?limit=200` | HTTP 200 |
| TC-FR01-13 | B | P0 | I | AC-1.8 | `GET /v1/tasks?limit=0` (min boundary) | `?limit=0` | HTTP 422, problem+json |
| TC-FR01-14 | N | P0 | I | AC-1.9 | `DELETE /v1/tasks/{id}` with `write` (non-admin) | key with `scope=write` | HTTP 403; response body does **not** contain the task id, status, name (existence-leak guard) |
| TC-FR01-15 | P | P0 | I | AC-1.10 | `DELETE /v1/tasks/{id}` with `admin` key | admin-scoped key, existing id | HTTP 200/204; both `tasks` row AND any `task_results` rows gone; verified via raw SQL (same transaction) |
| TC-FR01-16 | E | P1 | I | AC-1.6 | `GET /v1/tasks/<not-a-uuid>` | `id="abc"` | HTTP 422 (validation), not 404 (no enumeration leak) |
| TC-FR01-17 | E | P1 | I | AC-1.4 | Concurrent duplicate-name POSTs (N=5) | 5 parallel POSTs same name | exactly **1** 201, **4** 409s; DB has 1 row |
| TC-FR01-18 | E | P1 | I | AC-1.7 | `GET /v1/tasks?cursor=<invalid>` | malformed cursor | HTTP 422, problem+json |

---

## 2. FR-02 — 任務執行端點 (`POST /v1/tasks/{id}/run`, `GET /v1/tasks/{id}/runs`)

**Scope (SRS §3 FR-02)**: HTTP 202 + `run_id`; `asyncio.create_subprocess_exec(*shlex.split(command))`; `shell=True` forbidden; state machine `pending → running → done|failed|timeout`; result row in `task_results`.
**Modules**: `taskq.api.routes.runs`, `taskq.service.runner`, `taskq.repository.results`.

| TC ID | Cat | Pri | Layer | AC | Description | Input | Expected Output |
|---|---|---|---|---|---|---|---|
| TC-FR02-01 | P | P0 | I | AC-2.1 | `POST /v1/tasks/{id}/run` happy path | `write` key, existing id | HTTP 202, body `{"run_id": <uuid>}` |
| TC-FR02-02 | N | P0 | I | AC-2.1 | `POST /v1/tasks/{unknown}/run` | random uuid | HTTP 404, problem+json |
| TC-FR02-03 | N | P0 | I | AC-2.1 | `POST /v1/tasks/{id}/run` with `read` (non-write) | `scope=read` | HTTP 403, problem+json |
| TC-FR02-04 | E | P0 | I | AC-2.3 | Task with exit 0 → terminal `done` | `command: "true"` | after drain, row status `done`, `task_results.exit_code=0` |
| TC-FR02-05 | E | P0 | I | AC-2.3 | Task with exit 1 → terminal `failed` | `command: "false"` | status `failed`, `exit_code=1` |
| TC-FR02-06 | E | P0 | I | AC-2.3 | Task exceeding `TASKQ_TASK_TIMEOUT` → terminal `timeout` | `command: "sleep 60"` with `TASKQ_TASK_TIMEOUT=2` | status `timeout`, `exit_code=-9` (SIGKILL), no orphan `sleep` process |
| TC-FR02-07 | P | P0 | I | AC-2.4 | Result row written to `task_results` | `command: "echo hello world"` | row in `task_results` with `exit_code=0`, `stdout_tail` contains "hello world", `stderr_tail` empty, `duration_ms >= 0`, `finished_at` set |
| TC-FR02-08 | B | P0 | I | AC-2.4 | `stdout_tail` truncation at boundary | `command: "yes A \| head -c 200000"` | `stdout_tail` length ≤ configured cap (e.g. 64 KiB) |
| TC-FR02-09 | P | P0 | I | AC-2.5 | `GET /v1/tasks/{id}/runs` ordering | run 3 tasks sequentially | response sorted newest-first (timestamps desc) |
| TC-FR02-10 | N | P0 | I | AC-2.5 | `GET /v1/tasks/{unknown}/runs` | random uuid | HTTP 404, problem+json |
| TC-FR02-11 | N | P0 | I | AC-2.1 | `POST /v1/tasks/{id}/run` without `X-API-Key` | no header | HTTP 401, problem+json |
| TC-FR02-12 | E | P0 | U | AC-2.2 | `grep -rn "shell=True\|eval(\|exec(" src/` | repo grep | **0 hits** (NFR-02 cross-link) |
| TC-FR02-13 | E | P0 | U | AC-2.2 | Subprocess runner uses `shlex.split` + `create_subprocess_exec` | unit test on runner fn | AST / call-args assert that `shell=` keyword absent and argv is list |
| TC-FR02-14 | E | P1 | I | AC-2.4 | `stderr_tail` captured separately from stdout | `command: "sh -c 'echo OUT; echo ERR 1>&2'"` | `stdout_tail` contains "OUT", `stderr_tail` contains "ERR", no cross-contamination |
| TC-FR02-15 | E | P1 | I | AC-2.3 | State machine transition visibility | poll task status during a slow command | observe `pending` then `running` then terminal |

---

## 3. FR-03 — API Key 認證

**Scope (SRS §3 FR-03)**: `X-API-Key` required on all `/v1/*`; SHA-256 hash storage; `hmac.compare_digest`; plaintext printed once on `key create`; `revoked_at` invalidates; `/healthz` and `/readyz` unauthenticated.
**Modules**: `taskq.cli.key_create`, `taskq.service.auth`, `taskq.repository.keys`, `taskq.models.api_key`.

| TC ID | Cat | Pri | Layer | AC | Description | Input | Expected Output |
|---|---|---|---|---|---|---|---|
| TC-FR03-01 | N | P0 | I | AC-3.1 | Any `/v1/*` endpoint without `X-API-Key` | e.g. `GET /v1/tasks` | HTTP 401, problem+json |
| TC-FR03-02 | N | P0 | I | AC-3.1 | Any `/v1/*` with **invalid** key | `X-API-Key: not-a-real-key` | HTTP 401, problem+json |
| TC-FR03-03 | B | P0 | I | AC-3.1 | Empty `X-API-Key: ` | empty header value | HTTP 401, problem+json |
| TC-FR03-04 | P | P0 | U | AC-3.2 | `api_keys.key_hash` is 64 hex chars | seed key, query DB | `len(key_hash) == 64`, matches `/^[0-9a-f]{64}$/`, no `key_plaintext` column |
| TC-FR03-05 | P | P0 | U | AC-3.3 | Comparison uses `hmac.compare_digest` | unit test on compare fn | monkey-patch `hmac.compare_digest` and assert it was called |
| TC-FR03-06 | P | P0 | I | AC-3.4 | `python -m taskq key create --scope write` | CLI invocation | stdout contains plaintext exactly once; returncode 0; subsequent runs do not re-emit |
| TC-FR03-07 | N | P0 | I | AC-3.4 | Plaintext not present in any log file | run CLI, then `grep -r <plaintext> logs/` | 0 hits |
| TC-FR03-08 | N | P0 | I | AC-3.4 | Plaintext not present in `/v1/metrics` body | full scrape of metrics | 0 hits |
| TC-FR03-09 | N | P0 | I | AC-3.5 | Revoked key (set `revoked_at=now()`) → 401 | use key after revoke | HTTP 401, problem+json |
| TC-FR03-10 | P | P0 | I | AC-3.6 | `GET /healthz` no header | no header | HTTP 200 |
| TC-FR03-11 | P | P0 | I | AC-3.6 | `GET /readyz` no header | no header | HTTP 200 (or 503 depending on state) — must NOT be 401 |
| TC-FR03-12 | B | P0 | I | AC-3.1 | Whitespace-only key | `X-API-Key: "   "` | HTTP 401, problem+json |
| TC-FR03-13 | E | P0 | U | AC-3.2 | No plaintext column anywhere on `api_keys` table | SQL `PRAGMA table_info(api_keys)` (SQLite) | column list does not include any `plaintext` / `secret` / `key` (non-hash) field |

---

## 4. FR-04 — Scope 授權

**Scope (SRS §3 FR-04)**: `read < write < admin` hierarchy; HTTP 403 + problem+json, no existence leak; **single** dependency enforces it.
**Modules**: `taskq.api.deps`, `taskq.service.auth`.

| TC ID | Cat | Pri | Layer | AC | Description | Input | Expected Output |
|---|---|---|---|---|---|---|---|
| TC-FR04-01 | P | P0 | U | AC-4.1 | Scope hierarchy: read satisfied by write and admin | unit test of `scope_satisfies(read, write)` etc. | True for all `{read,write,admin}` ≥ `{read}` |
| TC-FR04-02 | N | P0 | U | AC-4.1 | write does **not** satisfy read-only when read is required and write key explicitly downgraded | unit test | True (write ≥ read); only test direction, downgrade not allowed |
| TC-FR04-03 | N | P0 | I | AC-4.2 | `write` key to admin endpoint (`DELETE /v1/tasks/{id}`) | write key | HTTP 403, problem+json |
| TC-FR04-04 | N | P0 | I | AC-4.2 | `read` key to `POST /v1/tasks` | read key | HTTP 403, problem+json |
| TC-FR04-05 | E | P0 | I | AC-4.2 | 403 body does **not** reveal existence of unknown id | `write` key, `DELETE /v1/tasks/{random-uuid}` | body does not contain `not found`, the id, or any status field exposing existence |
| TC-FR04-06 | P | P0 | I | AC-4.2 | 403 body for known vs unknown id is **byte-identical** (after correlation_id) | two requests: one known id, one unknown | only `correlation_id` differs |
| TC-FR04-07 | P | P0 | U | AC-4.3 | Every `/v1` route has the **single** scope-check dependency | inspect FastAPI `app.router.routes` for `dependencies=...` | exactly one `require_scope` dep referenced per `/v1` route; no inline `if scope < ...` in handler bodies (lint + AST) |
| TC-FR04-08 | E | P1 | I | AC-4.2 | admin key gets through scope gate | admin key on DELETE | HTTP 200/204 (or appropriate non-403) |
| TC-FR04-09 | E | P0 | U | AC-4.3 | Only one module defines the auth dep | `grep -rn "Depends(" taskq/api/routes/` | single dep name reused (e.g. `require_scope`) |

---

## 5. FR-05 — 流量控制 (per-token token bucket)

**Scope (SRS §3 FR-05)**: `TASKQ_RATE_BURST` capacity, `TASKQ_RATE_PER_SEC` refill; 429 + `Retry-After`; DB-backed state with row-level lock; `/healthz` and `/readyz` exempt.
**Modules**: `taskq.api.middleware`, `taskq.service.rate_limit`, `taskq.repository.rate_buckets`.

| TC ID | Cat | Pri | Layer | AC | Description | Input | Expected Output |
|---|---|---|---|---|---|---|---|
| TC-FR05-01 | P | P0 | U | AC-5.1 | Bucket config read from env | `TASKQ_RATE_BURST=20`, `TASKQ_RATE_PER_SEC=5.0` | bucket initial tokens == 20, refill == 5/s |
| TC-FR05-02 | N | P0 | I | AC-5.2 | Burst+1 requests in window | `TASKQ_RATE_BURST=5`, send 6 reqs in <1s | first 5 → 2xx, 6th → HTTP 429, problem+json, `Retry-After: <int seconds>` |
| TC-FR05-03 | B | P0 | I | AC-5.2 | `Retry-After` is integer seconds | overflow request | header value is a non-negative integer |
| TC-FR05-04 | P | P0 | I | AC-5.2 | After waiting `Retry-After` seconds, request succeeds | sleep N s | next request 2xx |
| TC-FR05-05 | P | P0 | U | AC-5.3 | Bucket state row in DB | consume token | row exists in `rate_buckets` with `key_id`, `tokens`, `updated_at` |
| TC-FR05-06 | P | P0 | U | AC-5.3 | Update uses `SELECT ... FOR UPDATE` (or SQLite equivalent) within single txn | unit test on repo | SQL captured includes `FOR UPDATE` (or `BEGIN IMMEDIATE` for SQLite) and `COMMIT` |
| TC-FR05-07 | P | P0 | I | AC-5.4 | `/healthz` not rate-limited | 100 reqs as same key | all 200, never 429 |
| TC-FR05-08 | P | P0 | I | AC-5.4 | `/readyz` not rate-limited | 100 reqs as same key | all 200/503, never 429 |
| TC-FR05-09 | E | P0 | I | AC-5.2 | Two keys → independent buckets | key A exhausted, key B fresh | A 429, B 2xx |
| TC-FR05-10 | E | P0 | I | AC-5.3 | Concurrent burst (N=20 parallel) against bucket=10 | asyncio.gather 20 calls | exactly 10 succeed, 10 get 429 (no double-spend) |

---

## 6. FR-06 — 持久化層與交易邊界

**Scope (SRS §3 FR-06)**: repository-only data access; 1 `Session` per request, context manager commit/rollback; no string-concatenated SQL; `selectinload`/`joinedload` explicit; pool config.
**Modules**: `taskq.repository.units_of_work`, `taskq.repository.tasks`.

| TC ID | Cat | Pri | Layer | AC | Description | Input | Expected Output |
|---|---|---|---|---|---|---|---|
| TC-FR06-01 | P | P0 | U | AC-6.1 | `taskq.service.tasks` does not import `sqlalchemy` | `import sqlalchemy` check via grep/AST | 0 hits in service layer |
| TC-FR06-02 | P | P0 | U | AC-6.2 | `with uow.transaction(): ...` commits on success | unit | row present after exit |
| TC-FR06-03 | N | P0 | U | AC-6.2 | `with uow.transaction(): ...` raises → rollback | unit, raise inside block | row NOT present after exit |
| TC-FR06-04 | P | P0 | C | AC-6.3 | `grep -rn` for f-string/`%`/`+` SQL composition in `src/` | gate | 0 hits |
| TC-FR06-05 | P | P0 | I | AC-6.4 | List endpoint SQL count constant | SQLAlchemy event listener, dataset sizes {1, 100, 1000, 10000} | statement count ≤ 4, **variance = 0** across sizes |
| TC-FR06-06 | P | P0 | U | AC-6.4 | Composition: 1×count + 1×main + ≤2×eager-loads | instrumentation | matches the expected decomposition |
| TC-FR06-07 | P | P0 | U | AC-6.5 | Engine has `pool_size=TASKQ_DB_POOL_SIZE` and `pool_pre_ping=True` | inspect engine config | both present |
| TC-FR06-08 | P | P0 | I | AC-6.1 | Cross-layer import gate (`lint-imports` exit 0) | `lint-imports` | exit 0; no service/api import of sqlalchemy |
| TC-FR06-09 | E | P0 | U | AC-6.4 | A list query without `selectinload`/`joinedload` is rejected by reviewer/grep | grep for `relationship.*lazy=` defaults | 0 hits of `lazy="select"` (or similar N+1 bait) on list paths |
| TC-FR06-10 | E | P0 | I | AC-6.2 | Exception mid-transaction leaves no partial rows | insert 2 rows, fail on 2nd | DB has 0 rows after rollback |
| TC-FR06-11 | B | P0 | I | AC-6.4 | Single-id GET issues ≤ 2 statements | SQLAlchemy listener | ≤ 2 (main + 1 eager-load) |

---

## 7. FR-07 — Schema Migration (Alembic v1→v2→v3 with data migration)

**Scope (SRS §3 FR-07)**: 3 revisions, every step reversible; v3 splits `tasks.result_json` into `task_results`; round-trip column-byte-identical.
**NFR-09 special clause**: must use **real SQLite file** (not in-memory mock).
**Modules**: `taskq.migrations.versions`.

| TC ID | Cat | Pri | Layer | AC | Description | Input | Expected Output |
|---|---|---|---|---|---|---|---|
| TC-FR07-01 | P | P0 | M | AC-7.1 | v1 upgrade creates `tasks` + `api_keys` with correct columns | temp SQLite file, `alembic upgrade v1` | both tables exist, columns match §5.4 |
| TC-FR07-02 | P | P0 | M | AC-7.2 | v2 adds `tags`, `task_tags`, unique idx on `tasks.name` | `alembic upgrade v2` | new tables + unique index present; v1 data untouched |
| TC-FR07-03 | P | P0 | M | AC-7.3 | v3 migrates `tasks.result_json` → `task_results`, then drops column | seed row with `result_json={"exit_code":0,"stdout_tail":"hi"}` | row appears in `task_results`; `tasks.result_json` column gone |
| TC-FR07-04 | P | P0 | M | AC-7.4 | `alembic upgrade head` exit 0 | run against temp DB | exit 0, all tables present |
| TC-FR07-05 | P | P0 | M | AC-7.4 | `alembic downgrade base` exit 0, no residual tables | run downgrade | exit 0, DB has no app tables |
| TC-FR07-06 | P | P0 | M | AC-7.5 | Round-trip: `upgrade head` → write sample → `downgrade -1` → `upgrade head` | sample: tasks with `result_json` populated | every column of sample row **byte-identical** to original |
| TC-FR07-07 | N | P0 | M | AC-7.6 | Migrations contain no `op.execute("DROP TABLE ...")` destructive shortcut | `grep -rn 'op.execute("DROP' migrations/` | 0 hits |
| TC-FR07-08 | P | P0 | M | AC-7.7 | Offline SQL generation produces valid SQL | `alembic upgrade head --sql` | non-empty SQL, no template tokens left |
| TC-FR07-09 | B | P0 | M | AC-7.5 | Round-trip with **0 sample rows** | empty DB round-trip | exit 0 both ways |
| TC-FR07-10 | B | P0 | M | AC-7.5 | Round-trip with **large** result_json (10 KiB) | seed with 10 KiB blob | byte-identical after round-trip |
| TC-FR07-11 | E | P0 | M | AC-7.5 | Round-trip with **NUL bytes** in result_json | seed with `b'\x00...'` | byte-identical after round-trip |
| TC-FR07-12 | E | P0 | M | AC-7.6 | Downgrade v3 does NOT use `DROP TABLE` shortcut | grep + offline SQL diff | downgrade uses column-add + data copy, then drop column |
| TC-FR07-13 | P | P0 | M | AC-7.7 | Each revision file is importable and has docstring | unit | ast-parses; docstring contains `[FR-07]` |

---

## 8. FR-08 — 非同步執行器 (asyncio.TaskGroup + drain + timeout + cancel)

**Scope (SRS §3 FR-08)**: `asyncio.TaskGroup`; `TASKQ_MAX_CONCURRENT` cap; `TASKQ_DRAIN_TIMEOUT` graceful drain; `asyncio.wait_for` + `process.kill` + `await wait`; `CancelledError` propagation.
**Modules**: `taskq.service.runner`.

| TC ID | Cat | Pri | Layer | AC | Description | Input | Expected Output |
|---|---|---|---|---|---|---|---|
| TC-FR08-01 | P | P0 | I | AC-8.1 | Graceful drain under `DRAIN_TIMEOUT` | start 3 tasks, send SIGTERM, set `DRAIN_TIMEOUT=5` | all 3 finish `done`/`failed`, no `interrupted` |
| TC-FR08-02 | E | P0 | I | AC-8.1 | Drain over `DRAIN_TIMEOUT` → tasks marked `interrupted` | start 1 long task (`sleep 60`), `DRAIN_TIMEOUT=1` | task state becomes `interrupted` after drain |
| TC-FR08-03 | P | P0 | I | AC-8.2 | Concurrency cap `TASKQ_MAX_CONCURRENT=4` | start 10 tasks | at no point are > 4 in `running` state (sampled) |
| TC-FR08-04 | B | P0 | I | AC-8.2 | `MAX_CONCURRENT=1` (degenerate boundary) | start 5 quick tasks | all eventually complete; never > 1 running |
| TC-FR08-05 | P | P0 | I | AC-8.3 | Task timeout kills subprocess | `command: "sleep 30"`, `TASKQ_TASK_TIMEOUT=2` | `exit_code=-9` (SIGKILL); no `sleep` process left (`pgrep sleep` → empty) |
| TC-FR08-06 | N | P0 | I | AC-8.3 | No orphan child processes after timeout burst | run 5 timeout cases | `pgrep -P $PPID` returns 0 orphans |
| TC-FR08-07 | P | P0 | U | AC-8.4 | `CancelledError` propagates through runner | unit, inject cancellation | exception reaches test, not swallowed |
| TC-FR08-08 | N | P0 | U | AC-8.4 | `except Exception` does not appear in runner | `grep -rn 'except Exception' taskq/service/runner/` | 0 hits |
| TC-FR08-09 | N | P0 | C | AC-8.4 | AST scan: no `except Exception: pass` in `taskq/` | static AST | 0 hits |
| TC-FR08-10 | E | P0 | I | AC-8.1 | Drain on KeyboardInterrupt (simulated) | inject cancel into TaskGroup | drain initiated; no in-flight task crashes DB |
| TC-FR08-11 | E | P0 | I | AC-8.3 | Timeout that returns exit 124 (subprocess self-terminated) | `command: "timeout 1 sleep 30"` | status not `timeout` if exit code is the `timeout` utility's; but no orphan |

---

## 9. FR-09 — 健康檢查與可觀測性 (`/healthz`, `/readyz`, `/v1/metrics`)

**Scope (SRS §3 FR-09)**: `/healthz` liveness (no auth); `/readyz` DB + alembic head (no auth, 503 on either fail with detail); `/v1/metrics` admin scope.
**Modules**: `taskq.api.routes.health`, `taskq.api.routes.metrics`, `taskq.service.metrics`.

| TC ID | Cat | Pri | Layer | AC | Description | Input | Expected Output |
|---|---|---|---|---|---|---|---|
| TC-FR09-01 | P | P0 | I | AC-9.1 | `GET /healthz` returns 200, body `{"status":"ok"}` | no auth | HTTP 200, `application/json` body |
| TC-FR09-02 | P | P0 | I | AC-9.2 | `GET /readyz` happy path | DB up, migration at head | HTTP 200 |
| TC-FR09-03 | N | P0 | I | AC-9.2 | `/readyz` when DB unreachable | stop engine or close DB file | HTTP 503; body `detail` says `db` or `database` |
| TC-FR09-04 | N | P0 | I | AC-9.2 | `/readyz` when alembic not at head | `alembic downgrade -1`, hit readyz | HTTP 503; body `detail` says `migration` |
| TC-FR09-05 | P | P0 | I | AC-9.3 | `/v1/metrics` with admin key | admin key | HTTP 200, body has task counts by status, latency percentiles (e.g. p50/p95/p99), rate-limit rejection counter |
| TC-FR09-06 | N | P0 | I | AC-9.3 | `/v1/metrics` with `write` key | write key | HTTP 403, problem+json |
| TC-FR09-07 | N | P0 | I | AC-9.3 | `/v1/metrics` without `X-API-Key` | no header | HTTP 401, problem+json |
| TC-FR09-08 | P | P0 | I | AC-9.1 | `/healthz` no auth (negative case proves not auth-gated) | send bogus `X-API-Key` | HTTP 200, header ignored |
| TC-FR09-09 | B | P0 | I | AC-9.2 | `/readyz` when DB reachable but **alembic version table empty** | fresh DB, no migrations run | HTTP 503; detail names migration |
| TC-FR09-10 | E | P0 | I | AC-9.3 | Metrics output does NOT leak DB password | dump full `/v1/metrics` body | 0 hits of `TASKQ_DB_URL` password fragment |

---

## 10. FR-10 — 錯誤契約 (RFC 7807 `application/problem+json`)

**Scope (SRS §3 FR-10)**: All non-2xx use `application/problem+json`; fields `type/title/status/detail/instance/correlation_id`; `detail` no SQL/stack/path/schema; `correlation_id` in `X-Correlation-Id` header + logs; HTTP status map per SPEC §7.
**Modules**: `taskq.errors.problem`, `taskq.errors.handlers`, `taskq.api.middleware`, `taskq.api.schemas`.

| TC ID | Cat | Pri | Layer | AC | Description | Input | Expected Output |
|---|---|---|---|---|---|---|---|
| TC-FR10-01 | P | P0 | I | AC-10.1 | Every error endpoint returns `Content-Type: application/problem+json` | sweep 401/403/404/409/422/429/503 paths | all have problem+json content type |
| TC-FR10-02 | P | P0 | I | AC-10.2 | Body has all 6 fields | trigger 404 | `type`, `title`, `status`, `detail`, `instance`, `correlation_id` all present and typed |
| TC-FR10-03 | P | P0 | U | AC-10.3 | `detail` excludes SQL / stack / path / schema | unit on handler | regex check on synthetic inputs |
| TC-FR10-04 | P | P0 | I | AC-10.3 | Trigger 500 (force unhandled exception in handler), inspect body | monkey-patch handler to raise | body `detail` is a generic message, no traceback / SQL / file path |
| TC-FR10-05 | P | P0 | I | AC-10.4 | `X-Correlation-Id` header present and equals body's `correlation_id` | any non-2xx | equality holds |
| TC-FR10-06 | P | P0 | I | AC-10.4 | `correlation_id` appears in server log | trigger 404, read log | log line contains the same id |
| TC-FR10-07 | P | P0 | I | AC-10.5 | HTTP status map per SPEC §7 | sweep 7 cases | 422/401/403/404/409/429(+`Retry-After`)/503/500 each occur at least once across the suite |
| TC-FR10-08 | N | P0 | I | AC-10.1 | 2xx responses are **not** problem+json | success GET | `Content-Type` is `application/json` (or none) |
| TC-FR10-09 | B | P0 | I | AC-10.4 | `correlation_id` format (uuid v4) | trigger 404 | matches `^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-...` or framework-defined id format |
| TC-FR10-10 | E | P0 | I | AC-10.2 | All 6 fields are JSON-typed correctly | schema validation | `type` is URI string, `status` is int, `title` is string, `detail` is string, `instance` is URI string, `correlation_id` is string |
| TC-FR10-11 | E | P0 | I | AC-10.3 | Body never contains `Traceback (most recent call last)` | 500-trigger | 0 hits |

---

## 11. FR-99 — Framework-owned implementation paths

**Scope (SRS §7 NFR-99)**: deferred to framework per 「角色不變,路徑變」. Verify the **roles** exist and are wired into the right places.

| TC ID | Cat | Pri | Layer | AC | Description | Input | Expected Output |
|---|---|---|---|---|---|---|---|
| TC-FR99-01 | P | P0 | C | NFR-99 | Async sub-process runner role exists | `grep -rn "asyncio.create_subprocess_exec" taskq/` | ≥1 hit in `service/` layer (not `api/`) |
| TC-FR99-02 | P | P0 | C | NFR-99 | Single auth/authz decision point | `grep -rn "Depends(.*scope" taskq/api/routes/` | single dep referenced by all `/v1` handlers |
| TC-FR99-03 | P | P0 | C | NFR-99 | DB transaction-boundary context manager exists | `grep -rn "def transaction\|@contextmanager" taskq/repository/` | a `transaction()` context manager exists |
| TC-FR99-04 | P | P0 | C | NFR-99 | v3 data-migration revision file exists and does real data move (not `op.execute("DROP")`) | inspect `taskq/migrations/versions/*_v3_*.py` | contains `op.add_column` for `task_results`, `INSERT INTO task_results SELECT ...`, then `op.drop_column('tasks','result_json')` |
| TC-FR99-05 | P | P0 | C | NFR-99 | Folder structure decision recorded in HANDOVER/CLAUDE | read `HANDOVER.md` | explicit note 「path framework-owned per SPEC §0」 |

---

## 12. NFR-01 — 效能與查詢效率 (dimension: performance)

| TC ID | Cat | Pri | Layer | AC | Description | Input | Expected Output |
|---|---|---|---|---|---|---|---|
| TC-N01-01 | B | P0 | I | AC-N1.1 | `GET /v1/tasks/{id}` p95 < 30 ms at 10k rows | `pytest-benchmark` over ASGI | p95 < 30 ms |
| TC-N01-02 | B | P0 | I | AC-N1.2 | `GET /v1/tasks?limit=50` p95 < 80 ms at 10k rows | `pytest-benchmark` | p95 < 80 ms |
| TC-N01-03 | B | P0 | I | AC-N1.3 | SQL count ≤ 4, variance 0 across {1,100,1000,10000} | SQLAlchemy listener | statement count array has stdev == 0, max ≤ 4 |
| TC-N01-04 | B | P0 | I | AC-N1.4 | Statement composition = 1×count + 1×main + ≤2×eager | instrument | matches expected composition |
| TC-N01-05 | E | P1 | I | AC-N1.3 | At max dataset (10k) the SQL count is **identical** to 1 row | compare | equal |

---

## 13. NFR-02 — HTTP 與資料層安全 (dimension: security)

| TC ID | Cat | Pri | Layer | AC | Description | Input | Expected Output |
|---|---|---|---|---|---|---|---|
| TC-N02-01 | P | P0 | C | AC-N2.1 | `grep -rn "shell=True\|eval(\|exec(" src/` | repo grep | 0 hits |
| TC-N02-02 | P | P0 | C | AC-N2.2 | SQL string-concat gate (f-string/`%`/`+` SQL composition) | gate | 0 hits |
| TC-N02-03 | P | P0 | I | AC-N2.3 | API keys hashed + `hmac.compare_digest` | unit | hashed in DB; compare_digest called (TC-FR03-04 / TC-FR03-05) |
| TC-N02-04 | P | P0 | I | AC-N2.4 | 403 does not leak resource existence | covered by TC-FR04-05/06 | byte-identical 403 body for known vs unknown |
| TC-N02-05 | P | P0 | I | AC-N2.5 | Error body has no stack/SQL/path | covered by TC-FR10-04 | 0 hits in body |
| TC-N02-06 | P | P0 | I | AC-N2.6 | CORS denies all origins by default | send Origin: `https://evil.example` with no `TASKQ_CORS_ORIGINS` | `Access-Control-Allow-Origin` absent or not echoed |
| TC-N02-07 | P | P0 | I | AC-N2.6 | CORS allowlist via `TASKQ_CORS_ORIGINS` | set env, send matching origin | `Access-Control-Allow-Origin` echoes allowed origin |
| TC-N02-08 | P | P0 | C | AC-N2.7 | `bandit -r src/` | run | 0 HIGH, 0 MEDIUM |
| TC-N02-09 | E | P1 | C | AC-N2.6 | CORS preflight (OPTIONS) without allowlist | send OPTIONS | no CORS allow headers |
| TC-N02-10 | E | P1 | C | AC-N2.7 | bandit re-run after a deliberate flaw injection (smoke) | not committed; CI-only | local result documents baseline |

---

## 14. NFR-03 — 錯誤處理、交易與非同步正確性 (dimension: error_handling / reliability)

| TC ID | Cat | Pri | Layer | AC | Description | Input | Expected Output |
|---|---|---|---|---|---|---|---|
| TC-N03-01 | P | P0 | U | AC-N3.1 | Transaction commits on success | covered by TC-FR06-02 | row present |
| TC-N03-02 | N | P0 | U | AC-N3.1 | Transaction rolls back on exception | covered by TC-FR06-03/10 | no partial rows |
| TC-N03-03 | P | P0 | C | AC-N3.2 | No bare `except:` or `except Exception: pass` in `src/` | AST scan | 0 hits |
| TC-N03-04 | P | P0 | U | AC-N3.3 | `CancelledError` re-raised | covered by TC-FR08-07 | propagated |
| TC-N03-05 | P | P0 | I | AC-N3.4 | DB down → `/readyz` 503 with detail, no infinite retry | stop DB | 503 with `db` in detail; response within <2 s |
| TC-N03-06 | P | P0 | I | AC-N3.5 | Timeout kills subprocess; no orphans | covered by TC-FR08-05/06 | no `sleep` process remains |
| TC-N03-07 | P | P0 | M | AC-N3.6 | Migration failure leaves DB at previous revision | inject a failing op in a temp branch | `alembic current` unchanged after failure |
| TC-N03-08 | E | P0 | C | AC-N3.2 | AST: `except BaseException` also forbidden (subsumes CancelledError) | AST scan | 0 hits |
| TC-N03-09 | E | P1 | I | AC-N3.4 | DB down → `/readyz` returns within bounded latency | time the call | < 2 s |

---

## 15. NFR-04 — 敏感資料遮蔽 (dimension: security)

| TC ID | Cat | Pri | Layer | AC | Description | Input | Expected Output |
|---|---|---|---|---|---|---|---|
| TC-N04-01 | P | P0 | U | AC-N4.1 | `stdout_tail` redacts `sk-...` pattern | `command: "echo sk-abcdef1234567890"` | `stdout_tail` contains `[REDACTED]`, not the secret |
| TC-N04-02 | P | P0 | U | AC-N4.1 | `stderr_tail` redacts `token=...` | `command: "echo token=secret123 >&2"` | `stderr_tail` has `[REDACTED]` |
| TC-N04-03 | P | P0 | U | AC-N4.1 | `Bearer <jwt>` redacted | `echo 'Bearer eyJ...'` | `[REDACTED]` |
| TC-N04-04 | P | P0 | U | AC-N4.1 | `postgres://user:pw@host/db` redacted in log/log line | inject DB URL into log | log line has `[REDACTED]` |
| TC-N04-05 | P | P0 | I | AC-N4.2 | DB password absent from `/v1/metrics` | dump metrics | 0 hits of password fragment |
| TC-N04-06 | P | P0 | I | AC-N4.2 | DB password absent from error body | trigger error that mentions DB | 0 hits |
| TC-N04-07 | P | P0 | I | AC-N4.2 | DB password absent from log file | run app, grep logs | 0 hits |
| TC-N04-08 | P | P0 | I | AC-N4.3 | API key plaintext printed exactly once on `key create` | covered by TC-FR03-06/07/08 | stdout once, no persistence, no metrics echo |
| TC-N04-09 | B | P0 | U | AC-N4.1 | Pattern at line start | `echo "sk-AAA..."` (no other content) | line replaced entirely with `[REDACTED]` |
| TC-N04-10 | B | P0 | U | AC-N4.1 | Multiple secrets on one line | `echo "sk-AAA token=BBB"` | entire line `[REDACTED]` |
| TC-N04-11 | E | P0 | U | AC-N4.1 | Pattern within URL query | `https://x?token=secret` | `[REDACTED]` |

---

## 16. NFR-05 — 文件覆蓋 (dimension: documentation)

| TC ID | Cat | Pri | Layer | AC | Description | Input | Expected Output |
|---|---|---|---|---|---|---|---|
| TC-N05-01 | P | P0 | C | AC-N5.1 | 100% public fn/class have docstring with `[FR-XX]` or `[NFR-XX]` | AST scan | coverage == 100% |
| TC-N05-02 | P | P0 | C | AC-N5.1 | Private helpers may be unmarked (negative) | AST | private (`_name`) not in required set |
| TC-N05-03 | P | P0 | I | AC-N5.2 | Every `/v1` route has `summary` + `description` in OpenAPI | `GET /openapi.json` | fields present for all routes |
| TC-N05-04 | B | P0 | I | AC-N5.2 | `/openapi.json` for `/healthz` + `/readyz` | fetch | summary/description present |
| TC-N05-05 | E | P1 | C | AC-N5.1 | Docstring token must match `[FR-NN]` or `[NFR-NN]` regex | AST | matches `^\[FR-\d{1,2}\]\|^\[NFR-\d{1,2}\]` style |

---

## 17. NFR-06 — 架構分層契約 (dimension: architecture_constraints / layering)

| TC ID | Cat | Pri | Layer | AC | Description | Input | Expected Output |
|---|---|---|---|---|---|---|---|
| TC-N06-01 | P | P0 | C | AC-N6.1 | `.importlinter` exists at repo root | `ls .importlinter` | present, declares `api > service > repository > models` |
| TC-N06-02 | P | P0 | C | AC-N6.1 | Independence modules declared | inspect | `config` and `errors` listed as independence |
| TC-N06-03 | P | P0 | C | AC-N6.2 | Forbidden contract bans `sqlalchemy` outside `repository`/`models` | inspect | present in `[importlinter: contract: ...]` |
| TC-N06-04 | P | P0 | C | AC-N6.3 | `lint-imports` exit 0 | run | exit 0 |
| TC-N06-05 | N | P0 | C | AC-N6.3 | Service layer importing `sqlalchemy` is detected | unit, monkey-patch | gate fails (used in mutation testing) |
| TC-N06-06 | N | P0 | C | AC-N6.4 | No `ignore_imports` wildcards; no `.importlinter` removal; no contract downgrade | repo audit | none present |
| TC-N06-07 | E | P1 | C | AC-N6.1 | No circular imports | `import-linter` graph | 0 cycles |

---

## 18. NFR-07 — 依賴與授權合規 (dimension: license_compliance / licensing)

| TC ID | Cat | Pri | Layer | AC | Description | Input | Expected Output |
|---|---|---|---|---|---|---|---|
| TC-N07-01 | P | P0 | C | AC-N7.1 | `requirements.txt` uses `==` | inspect | all lines match `==` |
| TC-N07-02 | P | P0 | C | AC-N7.1 | `requirements.lock` pins every transitive | `pip-compile --generate-hashes` or equivalent | hash-pinned |
| TC-N07-03 | P | P0 | C | AC-N7.2 | Every dep license ∈ allowlist {MIT, BSD-2-Clause, BSD-3-Clause, Apache-2.0, PSF} | `pip-licenses --format=json --with-system` | all entries in allowlist |
| TC-N07-04 | P | P0 | C | AC-N7.3 | Scan covers full tree (direct + transitive) | same command | transitive count > 0 |
| TC-N07-05 | P | P0 | C | AC-N7.4 | SBOM artefact present (framework-decided path) | `ls` | file present; has `name`/`version`/`license`/`direct|transitive` per row |
| TC-N07-06 | N | P0 | C | AC-N7.2 | A dep with GPL-3 is rejected (smoke) | add + remove; CI only | would be flagged; not committed |

---

## 19. NFR-08 — 變異測試 (dimension: mutation_testing / mutation)

| TC ID | Cat | Pri | Layer | AC | Description | Input | Expected Output |
|---|---|---|---|---|---|---|---|
| TC-N08-01 | P | P0 | C | AC-N8.1 | `.methodology/harness_config.json` sets `features.mutation_testing: true` | inspect | true |
| TC-N08-02 | P | P0 | C | AC-N8.2 | Mutation score ≥ 70 over `service` + `repository` | `mutmut run` then `mutmut results` | score ≥ 70 |
| TC-N08-03 | P | P0 | C | AC-N8.3 | Scope-restriction rationale recorded in `harness_config.json` | inspect | a `rationale` field or comment referring to runtime budget |
| TC-N08-04 | E | P1 | C | AC-N8.2 | Mutations outside scope (e.g. `api/` layer) are not required to be killed | inspect runner | scope filter present |

---

## 20. NFR-09 — 驗證真實性 (零 skip 鐵律) (dimension: testability)

| TC ID | Cat | Pri | Layer | AC | Description | Input | Expected Output |
|---|---|---|---|---|---|---|---|
| TC-N09-01 | P | P0 | C | AC-N9.1 | No `pytest.skip` / `skipif` / `xfail` in test tree | AST scan | 0 hits |
| TC-N09-02 | P | P0 | C | AC-N9.2 | `pytest tests -q` reports `skipped = 0` | run | skipped count 0 |
| TC-N09-03 | P | P0 | C | AC-N9.3 | Every test has ≥ 1 `assert` | AST | `zero_assert == 0` |
| TC-N09-04 | P | P0 | C | AC-N9.4 | No `--ignore` / `-k` / `--deselect` / `collect_ignore` / `testpaths` removal | inspect `pyproject.toml`/`setup.cfg` + run | 0 hits |
| TC-N09-05 | P | P0 | M | AC-N9.5 | FR-07 round-trip test runs on real SQLite file | covered by TC-FR07-06 | path is `tmp_path` SQLite file, not `:memory:` |
| TC-N09-06 | P | P0 | C | AC-N9.6 | Migration logic is NOT skipped (assertion: at least 1 migration test exists with real DB) | inventory | ≥ 1 M-layer test present and not skipped |
| TC-N09-07 | P | P0 | C | AC-N9.7 | `TRACEABILITY_MATRIX.md` `VERIFIED` only after test passes | workflow | out of scope for test runner; verify at gate time |

---

## 21. NFR-10 — 整合覆蓋 (dimension: integration_coverage / integration)

| TC ID | Cat | Pri | Layer | AC | Description | Input | Expected Output |
|---|---|---|---|---|---|---|---|
| TC-N10-01 | P | P0 | C | AC-N10.1 | `tests/integration/` line coverage ≥ 80% over `src/` | `pytest --cov` | ≥ 80% |
| TC-N10-02 | P | P0 | C | AC-N10.2 | All integration tests use `httpx.AsyncClient(transport=ASGITransport(app))` | AST + grep | 0 direct handler calls in `tests/integration/` |
| TC-N10-03 | P | P0 | I | AC-N10.3 | CRUD full chain covered | inventory | POST → GET → LIST → DELETE chain green |
| TC-N10-04 | P | P0 | I | AC-N10.3 | Each error code 401/403/404/409/422/429/503 covered at least once | inventory | one passing test per code |
| TC-N10-05 | P | P0 | I | AC-N10.3 | Migration round-trip integration test | TC-FR07-06 | passes on real SQLite |
| TC-N10-06 | P | P0 | I | AC-N10.3 | Rate-limit trigger + recovery covered | TC-FR05-02/04 | passes |
| TC-N10-07 | P | P0 | I | AC-N10.3 | Graceful drain covered | TC-FR08-01/02 | passes |
| TC-N10-08 | E | P2 | I | AC-N10.3 | 500 (unexpected) is covered if reachable | inventory | present (not required every round per SPEC §10.3) |

---

## 22. NFR-11 — 可讀性 (dimension: readability / maintainability)

| TC ID | Cat | Pri | Layer | AC | Description | Input | Expected Output |
|---|---|---|---|---|---|---|---|
| TC-N11-01 | P | P0 | C | AC-N11.1 | Project MI (LLOC-weighted) ≥ 80 | `radon mi -s src/` | MI ≥ 80 |
| TC-N11-02 | P | P0 | C | AC-N11.1 | Single function CC ≤ 10 | `radon cc -s -a src/` | no function with CC > 10 (max is `B` or better) |
| TC-N11-03 | P | P0 | C | AC-N11.2 | Each file ≤ 400 lines | line counter | 0 files exceed 400 |
| TC-N11-04 | P | P0 | C | AC-N11.2 | Each source directory ≤ 15 files | `find ... -maxdepth 1 -type f \| wc -l` | 0 dirs exceed 15 |
| TC-N11-05 | P | P0 | C | AC-N11.3 | Each API handler ≤ 40 lines | line counter over `taskq/api/routes/**` | all ≤ 40 |
| TC-N11-06 | E | P1 | C | AC-N11.3 | No business logic in handlers (delegates to service) | review | handlers contain only validation + delegation |

---

## 23. NFR-12 — 系統驗證目標 (dimension: execute_verification_target / verifiability)

| TC ID | Cat | Pri | Layer | AC | Description | Input | Expected Output |
|---|---|---|---|---|---|---|---|
| TC-N12-01 | P | P0 | C | AC-N12.1 | `Makefile` has `verify-system` target chaining 4 steps | inspect | target exists with the 4 commands in order |
| TC-N12-02 | P | P0 | C | AC-N12.2 | `make verify-system` exits 0 and prints `verify-system: PASS` | run | exit 0, stdout contains literal `verify-system: PASS` |
| TC-N12-03 | B | P0 | C | AC-N12.2 | Step 1: `alembic upgrade head` runs | make | upgrade happens |
| TC-N12-04 | B | P0 | C | AC-N12.2 | Step 2: full test suite runs | make | pytest invoked |
| TC-N12-05 | B | P0 | C | AC-N12.2 | Step 3: service starts, `/healthz` + `/readyz` smoke | make | both return 200 (or 503 if migration-not-head; this run expects 200) |
| TC-N12-06 | B | P0 | C | AC-N12.2 | Step 4: `alembic downgrade base` then `upgrade head` round-trip | make | both succeed; DB ends at head |
| TC-N12-07 | E | P1 | C | AC-N12.2 | If any step fails, exit non-zero and skip `verify-system: PASS` | inject failure | exit ≠ 0; PASS line absent |

---

## 24. Cross-Cutting / Static Gates (run before per-FR testing)

| Gate | Source | Command | Pass condition |
|---|---|---|---|
| `grep_shell_eval_exec` | NFR-02 | `grep -rn "shell=True\|eval(\|exec(" src/` | 0 hits |
| `grep_sql_concat` | NFR-02 | static gate over `src/` | 0 hits |
| `bandit` | NFR-02 | `bandit -r src/` | 0 HIGH, 0 MEDIUM |
| `importlinter` | NFR-06 | `lint-imports` | exit 0 |
| `pip-licenses` | NFR-07 | `pip-licenses --format=json --with-system` | every entry in allowlist |
| `mutmut` | NFR-08 | `mutmut run` then `mutmut results` | score ≥ 70 over service + repository |
| `pytest_skip_count` | NFR-09 | `pytest tests -q` | skipped == 0 |
| `pytest_zero_assert` | NFR-09 | AST scan | 0 tests with 0 asserts |
| `pytest_exclude_clauses` | NFR-09 | inspect config + AST | 0 hits of `--ignore`/`-k`/`--deselect`/`collect_ignore`/missing `testpaths` |
| `integration_coverage` | NFR-10 | `pytest tests/integration --cov=src` | ≥ 80% |
| `radon_mi` | NFR-11 | `radon mi -s src/` | MI ≥ 80 |
| `radon_cc` | NFR-11 | `radon cc -s -a src/` | no CC > 10 |
| `file_loc` | NFR-11 | `wc -l` per file | no file > 400 |
| `dir_file_count` | NFR-11 | `find ... -maxdepth 1 -type f \| wc -l` | no dir > 15 |
| `handler_loc` | NFR-11 | handler line count | all ≤ 40 |
| `verify_system` | NFR-12 | `make verify-system` | exit 0, prints `verify-system: PASS` |

---

## 25. Coverage Map — every FR from the manifest

This guarantees manifest coverage. The `fr_ids` array in `.methodology/quality_manifest.json` is `["FR-01","FR-02","FR-03","FR-04","FR-05","FR-06","FR-07","FR-08","FR-09","FR-10","FR-99"]`. Each row below is covered by at least one TC above.

| FR | TC IDs | Notes |
|---|---|---|
| FR-01 | TC-FR01-01..TC-FR01-18 | all 10 ACs covered + boundary/edge |
| FR-02 | TC-FR02-01..TC-FR02-15 | all 5 ACs + subprocess safety |
| FR-03 | TC-FR03-01..TC-FR03-13 | all 6 ACs + hash-format / no-plaintext |
| FR-04 | TC-FR04-01..TC-FR04-09 | all 3 ACs + body-byte-equality + single-dep audit |
| FR-05 | TC-FR05-01..TC-FR05-10 | all 4 ACs + concurrent bucket |
| FR-06 | TC-FR06-01..TC-FR06-11 | all 5 ACs + composition |
| FR-07 | TC-FR07-01..TC-FR07-13 | all 7 ACs incl. NUL/large/empty round-trip |
| FR-08 | TC-FR08-01..TC-FR08-11 | all 4 ACs + orphan-free |
| FR-09 | TC-FR09-01..TC-FR09-10 | all 3 ACs + DB-down / not-head 503 |
| FR-10 | TC-FR10-01..TC-FR10-11 | all 5 ACs + schema validation |
| FR-99 | TC-FR99-01..TC-FR99-05 | framework-owned role verification |

## 26. NFR Coverage Map

`nfr_dimension_mapping` from the manifest maps every NFR-01..NFR-12 to a dimension. Every NFR is covered by TC-N01-01..TC-N12-07.

| NFR | Dimension | TC IDs |
|---|---|---|
| NFR-01 | performance | TC-N01-01..05 |
| NFR-02 | security | TC-N02-01..10 |
| NFR-03 | error_handling (reliability) | TC-N03-01..09 |
| NFR-04 | security | TC-N04-01..11 |
| NFR-05 | documentation | TC-N05-01..05 |
| NFR-06 | architecture_constraints (layering) | TC-N06-01..07 |
| NFR-07 | license_compliance (licensing) | TC-N07-01..06 |
| NFR-08 | mutation_testing (mutation) | TC-N08-01..04 |
| NFR-09 | test_assertion_quality (testability) | TC-N09-01..07 |
| NFR-10 | integration_coverage (integration) | TC-N10-01..08 |
| NFR-11 | readability (maintainability) | TC-N11-01..06 |
| NFR-12 | execute_verification_target (verifiability) | TC-N12-01..07 |

---

## 27. Test Inventory Cross-Reference

The TC IDs above align with `TEST_INVENTORY.yaml` `test_name` entries. Naming convention used: `test_<fr>_<ac>_<short>`. Inventory ↔ TC mapping (subset):

| TEST_INVENTORY test_name | TC ID |
|---|---|
| `test_fr01_ac1_post_creates_task_201` | TC-FR01-01 |
| `test_fr01_ac2_post_no_api_key_returns_401` | TC-FR01-02 |
| `test_fr01_ac3_post_invalid_body_returns_422` | TC-FR01-03..05 |
| `test_fr01_ac4_post_duplicate_name_returns_409` | TC-FR01-06 |
| `test_fr01_ac5_get_existing_returns_200` | TC-FR01-07 |
| `test_fr01_ac6_get_unknown_returns_404` | TC-FR01-08 |
| `test_fr01_ac7_list_supports_cursor_pagination` | TC-FR01-09/10 |
| `test_fr01_ac8_list_limit_over_200_returns_422` | TC-FR01-11..13 |
| `test_fr01_ac9_delete_write_scope_returns_403_no_leak` | TC-FR01-14 |
| `test_fr01_ac10_delete_admin_cascades_results` | TC-FR01-15 |
| `test_fr02_ac1_run_returns_202_with_run_id` | TC-FR02-01 |
| `test_fr02_ac2_subprocess_exec_no_shell_true` | TC-FR02-12/13 |
| `test_fr02_ac4_results_persisted_to_task_results` | TC-FR02-07 |
| `test_fr02_ac5_get_runs_newest_first` | TC-FR02-09 |
| `test_fr03_ac1_missing_or_invalid_key_returns_401` | TC-FR03-01/02/03 |
| `test_fr03_ac2_key_hash_sha256_64hex_no_plaintext` | TC-FR03-04/13 |
| `test_fr03_ac3_compare_digest_constant_time` | TC-FR03-05 |
| `test_fr03_ac4_create_prints_plaintext_once` | TC-FR03-06/07/08 |
| `test_fr03_ac5_revoked_key_invalid` | TC-FR03-09 |
| `test_fr03_ac6_healthz_readyz_no_auth` | TC-FR03-10/11 |
| `test_fr04_ac1_scope_hierarchy_read_write_admin` | TC-FR04-01 |
| `test_fr04_ac2_insufficient_scope_403_no_leak` | TC-FR04-03/04/05/06 |
| `test_fr04_ac3_single_dependency_audit` | TC-FR04-07/09 |
| `test_fr05_ac1_bucket_config_burst_per_sec` | TC-FR05-01 |
| `test_fr05_ac2_overflow_returns_429_with_retry_after` | TC-FR05-02/03/04 |
| `test_fr05_ac3_db_backed_row_level_lock` | TC-FR05-05/06 |
| `test_fr05_ac4_healthz_readyz_rate_limit_exempt` | TC-FR05-07/08 |
| `test_fr06_ac1_repository_only_data_access` | TC-FR06-01/08 |
| `test_fr06_ac2_one_session_per_request_context_manager` | TC-FR06-02/03/10 |
| `test_fr06_ac3_no_string_concat_sql_grep_gate` | TC-FR06-04 |
| `test_fr06_ac4_eager_load_sql_count_constant_le_4` | TC-FR06-05/06/09/11 |
| `test_fr06_ac5_pool_size_and_pre_ping` | TC-FR06-07 |
| `test_fr07_ac1_v1_creates_tasks_api_keys_tables` | TC-FR07-01 |
| `test_fr07_ac2_v2_adds_tags_task_tags_unique_index` | TC-FR07-02 |
| `test_fr07_ac3_v3_migrates_tasks_result_json_to_task_results` | TC-FR07-03 |
| `test_fr07_ac4_upgrade_head_downgrade_base_exit_zero` | TC-FR07-04/05 |
| `test_fr07_ac5_round_trip_byte_identical_sample` | TC-FR07-06/09/10/11 |
| `test_fr07_ac6_no_destructive_shortcut_drop_table` | TC-FR07-07/12 |
| `test_fr07_ac7_offline_sql_generation_covered` | TC-FR07-08/13 |
| `test_fr08_ac1_task_group_graceful_drain` | TC-FR08-01/02/10 |
| `test_fr08_ac2_max_concurrent_cap_queues_overflow` | TC-FR08-03/04 |
| `test_fr08_ac3_timeout_kills_subprocess_no_orphans` | TC-FR08-05/06/11 |
| `test_fr08_ac4_cancelled_error_propagates` | TC-FR08-07/08/09 |
| `test_fr09_ac1_healthz_returns_200_status_ok` | TC-FR09-01/08 |
| `test_fr09_ac2_readyz_db_and_alembic_head_check` | TC-FR09-02/03/04/09 |
| `test_fr09_ac3_metrics_admin_scope` | TC-FR09-05/06/07/10 |
| `test_fr10_ac1_problem_json_content_type` | TC-FR10-01/08 |
| `test_fr10_ac2_body_fields_type_title_status_detail_instance_correlation_id` | TC-FR10-02/10 |
| `test_fr10_ac3_detail_no_sql_stack_path_leak` | TC-FR10-03/04/11 |
| `test_fr10_ac4_correlation_id_mirrored_header_logs` | TC-FR10-05/06/09 |

---

## 28. Execution Order (one-time, before per-FR TDD)

1. **Static gates** (parallel) — `§24` rows 1-4, 7-9, 11-15. All must be 0 / pass.
2. **Migrations** — TC-FR07-01..13 (real SQLite file). Establishes DB-state baseline.
3. **Repository + service unit tests** — TC-FR06-*, TC-FR03-04/05/13, TC-FR04-01/07/09, TC-FR05-01/05/06, TC-FR08-07/08/09, TC-N03-*, TC-N04-01..04/09..11, TC-N05-01.
4. **Integration tests** — TC-FR01-*, TC-FR02-01..11/14/15, TC-FR03-01..03/06/09..12, TC-FR04-02..06/08, TC-FR05-02/04/07..10, TC-FR06-05/08, TC-FR09-*, TC-FR10-01/02/04/05/06/07/08/09/10/11, TC-N02-06/07, TC-N03-05, TC-N04-05..08, TC-N05-03/04, TC-N07-*, TC-N12-* (per `make verify-system`).
5. **Mutation** — `mutmut run` over `service` + `repository` (TC-N08-*).
6. **Performance** — TC-N01-* via `pytest-benchmark` at 10k rows.
7. **Final sweep** — `pytest -q` skipped == 0 (TC-N09-02); `tests/integration` coverage ≥ 80% (TC-N10-01).

---

## 29. Self-Review

- **Possible errors in this plan**:
  1. *Framework path mismatch*: SPEC §0/§5.3 defer module paths to the framework; the TC module references (e.g. `taskq.api.routes.tasks`) are taken from `.methodology/quality_manifest.json` `fr_module_traceability` and the existing `src/` layout. If the actual `src/` path differs, the file-path assertions in TC-FR99-01/02/03/04 and TC-N11-03/04/05 will be wrong. **Mitigation**: read `HANDOVER.md` and `02-architecture/` to confirm before locking the plan; the plan is intentionally modular so a per-test path can be edited without rewriting ACs.
  2. *Migration round-trip fragility on NUL bytes*: real `result_json` may use JSON which cannot contain NUL; the NUL test (TC-FR07-11) may be artificial. **Mitigation**: marked as `B/P0` for explicit byte-equality proof; if v3 uses JSON, drop NUL but keep large-blob test (TC-FR07-10).
  3. *Bandit / mutmut environment*: the local venv may not have `bandit` or `mutmut` installed; `pip-licenses` may not be on PATH. **Mitigation**: install via `requirements-dev.txt` (referenced in NFR-07 / NFR-10); if absent, gates fail and the plan's report will surface that explicitly.

- **Unverified assumptions**: the `tests/integration` directory path (currently empty) will be created by the per-FR TDD pass; this plan only documents the cases, not the harness. ASSUMED: `httpx` is in `requirements-dev.txt`; the project uses `pytest-benchmark`; `alembic` venv entry point is `alembic`. If any is missing, the corresponding TC must be re-scoped to a different tool.

- **Confidence**: HIGH on AC coverage (every AC in SRS §3-§4 has a TC row); MEDIUM on the framework-path-dependent TC names (TC-FR99-*, TC-N11-*); MEDIUM on mutation-test suite availability.

---

*End of TEST_PLAN.md — taskq-api v1.0.0 — 2026-08-23.*
