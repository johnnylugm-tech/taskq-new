# Software Architecture Document (SAD) — taskq-api

> Phase: **P2 — Architecture** · Project: **taskq-api** · Source-of-truth: `SPEC.md` v1.0.0 (10 FR / 12 NFR)
>
> SPEC §6 (folder structure) was explicitly removed by the spec author — the framework (this document) decides the concrete directory layout, subject to NFR-06's layers contract, the ≤15 files/dir rule, and CRG cohesion principles.

---

## 1. Overview

`taskq-api` is an ASGI HTTP service that exposes a task-queue over REST: callers submit tasks, trigger their execution, query state, and inspect metrics. Persistence is relational; schema evolves through three Alembic revisions (one of which moves data, round-trip-verifiable); authentication is `X-API-Key` with per-token scope hierarchy; rate-limiting uses a per-token token bucket; subprocess execution is asynchronous with hard timeout and graceful drain.

The architecture is layered: `api > service > repository > models`, with `config` and `errors` as independence modules (no upward imports allowed). Entry points (`taskq.api.app`, `taskq.cli.main`) live inside hub-bearing directories, so each top-level community has internal edges compensating for the unavoidable external library edges. All non-2xx responses follow RFC 7807 `application/problem+json`; `detail` is whitelisted to never leak SQL, stack traces, or filesystem paths.

### 1.1 System Verification Target

> **Every exit gate (2, 3 and 4)**: the harness executes `make verify-system`. A non-zero exit fails the gate. The target name is fixed — the harness always calls `make verify-system`.

- **Makefile target**: `verify-system`
- **Exercises (real, not test doubles)**:
  1. `alembic upgrade head` against a real SQLite file (not in-memory)
  2. Full pytest run (`tests/unit` + `tests/integration`)
  3. Boot uvicorn against the delivered `taskq.api.app` ASGI factory, hit `/healthz` and `/readyz` via `httpx.AsyncClient(transport=ASGITransport(app))`
  4. `alembic downgrade -1` → `alembic upgrade head` (round-trip — proves FR-07 v3 data migration is reversible on real data)
- The migration round-trip step is the load-bearing verification: it executes `taskq.repository.units_of_work`, `taskq.migrations.versions.v3`, and `taskq.repository.results` against a real SQLite file, modules the unit-test suite has replaced with `autouse` stand-ins.

### 1.2 Source-of-truth Cross-References

| Concern | Owner |
|---|---|
| Functional requirements (FR-01..FR-10) | `SPEC.md` §3 / `SRS.md` |
| Non-functional requirements (NFR-01..NFR-12) | `SPEC.md` §4 / `SRS.md` |
| Layers contract | `SPEC.md` §3 FR-06 / `SPEC.md` §4 NFR-06 |
| Risk register | `SPEC.md` §9 |
| Verification matrix | `SPEC.md` §8 / `01-requirements/TRACEABILITY_MATRIX.md` |

---

## 2. Module Design

### 2.1 Layer & Directory Layout

```
src/taskq/                       # source root (3-6 source dirs, excluding tests & migrations)
├── __init__.py
├── config/                      # INDEPENDENCE module — NFR-06
│   ├── __init__.py
│   ├── settings.py              # HUB: get_config_snapshot(), validate_config()
│   └── env.py                   # TASKQ_* env loading (SPEC §5.1)
├── errors/                      # INDEPENDENCE module — NFR-06
│   ├── __init__.py
│   ├── problem.py               # HUB: problem_json() (RFC 7807 builders — FR-10)
│   └── handlers.py              # HUB: redact(), install_handlers() (NFR-04)
├── models/                      # LAYER 4 — bottom of stack (NFR-06)
│   ├── __init__.py
│   ├── base.py
│   ├── task.py                  # tasks table
│   ├── api_key.py               # api_keys table (NFR-04 key_hash)
│   ├── task_result.py           # task_results table (FR-07 v3)
│   ├── tag.py                   # tags + task_tags (FR-07 v2)
│   └── rate_bucket.py           # rate_buckets table (FR-05)
├── repository/                  # LAYER 3 — only layer allowed to import sqlalchemy (NFR-06)
│   ├── __init__.py
│   ├── units_of_work.py         # HUB: unit_of_work() context manager (FR-06)
│   ├── tasks.py                 # HUB: get_task(), list_tasks() — selectinload enforced (NFR-01)
│   ├── keys.py                  # api_keys CRUD (FR-03)
│   ├── results.py               # task_results CRUD (FR-02, FR-07)
│   └── rate_buckets.py          # row-locked bucket update (FR-05)
├── service/                     # LAYER 2 — business logic (NFR-06, NFR-08 mutation scope)
│   ├── __init__.py
│   ├── auth.py                  # HUB: authenticate_key(), authorize_scope() (FR-03, FR-04)
│   ├── tasks.py                 # HUB: create_task(), delete_task() (FR-01)
│   ├── rate_limit.py            # HUB: consume_token() (FR-05)
│   ├── runner.py                # HUB: run_subprocess(), drain() — high-risk (FR-08)
│   └── metrics.py               # counters + p95 ring buffer (FR-09)
├── api/                         # LAYER 1 — FastAPI surface (NFR-06, NFR-10 ASGI)
│   ├── __init__.py
│   ├── app.py                   # HUB: create_app() — ASGI factory
│   ├── deps.py                  # HUB: require_scope() — the single auth/scope dependency (FR-04)
│   ├── middleware.py            # HUB: rate_limit_middleware, cors_middleware (FR-05, NFR-02)
│   ├── schemas.py               # HUB: TaskCreate, TaskOut (pydantic — FR-10, NFR-05)
│   └── routes/
│       ├── __init__.py
│       ├── tasks.py             # POST/GET/DELETE /v1/tasks (FR-01)
│       ├── runs.py              # POST /v1/tasks/{id}/run, GET /v1/tasks/{id}/runs (FR-02)
│       ├── keys.py              # admin key rotation surface (FR-03)
│       ├── health.py            # /healthz, /readyz (FR-09)
│       └── metrics.py           # /v1/metrics (FR-09)
├── migrations/                  # Alembic — FR-07 v1/v2/v3 with downgrade
│   ├── env.py
│   ├── script.py.mako
│   └── versions/
│       ├── 0001_v1_tasks_and_keys.py
│       ├── 0002_v2_tags_and_unique_name.py
│       └── 0003_v3_split_result_into_task_results.py   # round-trip-verified (R1)
└── cli/                         # entry point: `python -m taskq.cli.main`
    ├── __init__.py
    ├── main.py                  # HUB: migrate/seed/healthcheck dispatch
    └── key_create.py            # FR-03 key generation (plaintext printed once)

tests/
├── unit/                        # mirrors src/ for isolated tests
└── integration/                 # httpx.ASGITransport end-to-end (NFR-10)
```

**Directory counts (excluding tests & migrations):** 6 source dirs (`config`, `errors`, `models`, `repository`, `service`, `api`) + `cli` entrypoint dir. All ≤15 files/dir; largest is `api/` at 11 files. No god-module — each file ≤400 lines (NFR-11) and ≤40 lines per API handler (NFR-11).

**Layers contract** (NFR-06 — enforced by `.importlinter`):
```
api  >  service  >  repository  >  models
                          ↑
                         cli (entry-point only; allowed_dependencies: service, repository, config)

config  : independence — no upward imports
errors  : independence — no upward imports
```
The arrow `>` means "may import"; downward is forbidden. This makes cycles structurally impossible at the import-linter level.

### 2.2 FR → Module Mapping

Every functional requirement (SPEC §3) maps to at least one module. High-risk modules per SPEC §10 are marked **★**.

| FR | Title | Owning Module(s) | Key Behaviors |
|----|-------|------------------|---------------|
| **FR-01** | Task CRUD API | `taskq.api.routes.tasks`, `taskq.service.tasks`, `taskq.repository.tasks`, `taskq.api.schemas` | POST/GET/DELETE + cursor pagination, 422/404/409 contract |
| **FR-02** | Task execution endpoint | `taskq.api.routes.runs`, `taskq.service.runner` ★, `taskq.repository.results` | `POST /v1/tasks/{id}/run` → 202, subprocess via `create_subprocess_exec`, timeout-kill, history list |
| **FR-03** | API key authentication | `taskq.cli.key_create`, `taskq.service.auth`, `taskq.repository.keys`, `taskq.models.api_key` | sha256 hash + `hmac.compare_digest` + `revoked_at` guard; plaintext printed once |
| **FR-04** | Scope authorization | `taskq.api.deps` ★, `taskq.service.auth` | single `Depends()` enforces `read<write<admin`; 403 body never reveals existence |
| **FR-05** | Rate limiting | `taskq.api.middleware`, `taskq.service.rate_limit`, `taskq.repository.rate_buckets` | per-token token bucket in row-locked tx, 429 + `Retry-After` |
| **FR-06** | Persistence & tx boundary | `taskq.repository.units_of_work` ★, `taskq.repository.tasks` | one Session per request via `unit_of_work()` ctx mgr, `selectinload`/`joinedload` enforced, no string SQL |
| **FR-07** | Alembic 3-step migration | `taskq.migrations.versions` ★ (file `0003_v3_*`), `taskq.repository.results` | v1 tables; v2 tags + unique name; v3 split `result_json` → `task_results` (data move, round-trip verified) |
| **FR-08** | Async executor | `taskq.service.runner` ★ | `asyncio.TaskGroup`, `TASKQ_MAX_CONCURRENT`, `wait_for`+`kill()`+`wait()`, graceful drain, `CancelledError` propagated |
| **FR-09** | Health & observability | `taskq.api.routes.health`, `taskq.api.routes.metrics`, `taskq.service.metrics` | `/healthz` liveness; `/readyz` DB+alembic head; `/v1/metrics` counters+p95 (admin) |
| **FR-10** | RFC 7807 error contract | `taskq.errors.problem`, `taskq.errors.handlers`, `taskq.api.middleware`, `taskq.api.schemas` | every non-2xx is `application/problem+json`; `correlation_id` in header+log; `detail` whitelist |

**Cross-reference (every FR covered):** FR-01..FR-10 all present; no FR mapped to a missing module.

### 2.3 Module Specifications

#### 2.3.1 `taskq.api` (layer 1 — entry surface)

| Attribute | Value |
|---|---|
| Responsibility | HTTP surface, ASGI factory, dependency wiring, middleware, route registration |
| External interface | FastAPI app, OpenAPI at `/openapi.json`, JSON over HTTP at `/v1/*` |
| Dependencies | `taskq.service`, `taskq.errors`, `taskq.config` (never `repository` or `models` directly — NFR-06 forbidden contract) |

**Logical constraints**
- No `sqlalchemy.*` import (NFR-06 forbidden contract; verified by `lint-imports`)
- Single `require_scope` dependency used by every `/v1/*` route (FR-04 — test asserts every router imports it)
- Every handler ≤40 lines (NFR-11); non-trivial logic delegates to `taskq.service.*`

#### 2.3.2 `taskq.service` (layer 2 — business logic)

| Attribute | Value |
|---|---|
| Responsibility | Domain logic; orchestrations across repositories; subprocess execution |
| External interface | Plain Python functions called from `taskq.api` and `taskq.cli` |
| Dependencies | `taskq.repository`, `taskq.errors`, `taskq.config` |

**Logical constraints**
- No `sqlalchemy.*` import (NFR-06)
- Mutation testing scope (NFR-08): every public function in this layer must have unit tests
- `service.runner` is the single owner of `asyncio.create_subprocess_exec`; banned: `shell=True`, `eval(`, `exec(` (NFR-02 grep gate)

#### 2.3.3 `taskq.repository` (layer 3 — data access)

| Attribute | Value |
|---|---|
| Responsibility | All ORM access; explicit transaction boundaries; N+1 prevention |
| External interface | Functions returning ORM objects or scalars; no Session objects leak out |
| Dependencies | `taskq.models`, `taskq.errors`, `taskq.config` |

**Logical constraints**
- Sole layer permitted to import `sqlalchemy.*` (NFR-06)
- Every list-returning query uses `selectinload`/`joinedload` for known relationships (NFR-01)
- `units_of_work.unit_of_work()` is the only context manager that opens a `Session` (FR-06)

#### 2.3.4 `taskq.models` (layer 4 — ORM definitions)

| Attribute | Value |
|---|---|
| Responsibility | Declarative ORM classes mirroring the schema in SPEC §5.2 |
| External interface | `Base`, mapped classes consumed only by `taskq.repository` and `taskq.migrations` |
| Dependencies | none |

**Logical constraints**
- No business logic, no I/O (FR-06)
- Tables exactly match `SPEC.md` §5.2: `tasks`, `api_keys`, `tags`, `task_tags`, `task_results`, `rate_buckets`

#### 2.3.5 `taskq.errors` (independence module)

| Attribute | Value |
|---|---|
| Responsibility | RFC 7807 `application/problem+json` builders; secret-redaction filter; exception handler installation |
| External interface | `problem_json(type, title, status, detail, instance)`, `redact(text)` |
| Dependencies | none |

**Logical constraints**
- `redact()` applies the NFR-04 regex (`sk-[A-Za-z0-9_-]{8,}|token=\S+|Bearer\s+\S+|postgres(ql)?://[^\s]+`) line-level
- `detail` parameter is treated as opaque text — no stack/SQL/path may reach it; handlers are responsible for sanitizing upstream exceptions

#### 2.3.6 `taskq.config` (independence module)

| Attribute | Value |
|---|---|
| Responsibility | Read `TASKQ_*` env vars (SPEC §5.1); validate at startup; produce a `Settings` snapshot |
| External interface | `get_config_snapshot() -> Settings`, `validate_config(s)` |
| Dependencies | none |

**Logical constraints**
- Hub function `get_config_snapshot()` called at module load + at every test fixture boundary so internal edges accumulate (CRG principle)
- `Settings.db_url` is **never** logged or returned in any error path (NFR-04)

#### 2.3.7 `taskq.cli` (entry point — runs `python -m taskq.cli.main`)

| Attribute | Value |
|---|---|
| Responsibility | Operational subcommands: `migrate`, `seed`, `healthcheck`, `key create` |
| External interface | Argparse CLI; entry point per SPEC §1 |
| Dependencies | `taskq.service`, `taskq.repository`, `taskq.config` |

**Logical constraints**
- `key_create` is the only code path allowed to print plaintext API keys (NFR-02 / FR-03)

---

## 3. Interfaces & Data Flows

### 3.1 Component Diagram

```
                       ┌──────────────────────────────────────────┐
   X-API-Key           │           taskq.api (layer 1)            │
   (unauthenticated)──▶│  app.py → routes/* → deps.py → middleware│
                       └────────────┬──────────────┬──────────────┘
                                    │              │
                          (require_scope,           │ (problem_json,
                           rate_limit_mw)           │  redact)
                                    │              │
                                    ▼              ▼
                       ┌──────────────────────────────────────────┐
                       │          taskq.service (layer 2)         │
                       │  auth · tasks · rate_limit · runner · me… │
                       └────┬─────────────────┬───────────────────┘
                            │                 │
                (unit_of_work,                │
                 CRUD fns)                    │
                            │                 │
                            ▼                 ▼
              ┌─────────────────────┐  ┌──────────────────────────┐
              │ taskq.repository    │  │ taskq.errors (indep)     │
              │ (layer 3)           │  │  problem · handlers      │
              │  units_of_work ·    │  └──────────────────────────┘
              │  tasks · keys ·     │  ┌──────────────────────────┐
              │  results ·          │  │ taskq.config (indep)     │
              │  rate_buckets       │  │  settings · env          │
              └────────┬────────────┘  └──────────────────────────┘
                       │
                       ▼
              ┌─────────────────────┐
              │ taskq.models        │
              │ (layer 4 — ORM)     │
              └────────┬────────────┘
                       │
                       ▼
                ┌──────────────┐    alembic upgrade/downgrade
                │  SQLite/PG   │◀─────────────────────────────┐
                └──────────────┘                             │
                       ▲                                     │
                       │      taskq.migrations.versions       │
                       │      (v1, v2, v3 round-trip)         │
                       └─────────────────────────────────────┘
```

### 3.2 Request Lifecycle (POST /v1/tasks — FR-01)

```
client ──POST──▶ FastAPI router (taskq.api.routes.tasks)
                    │
                    ▼
              require_scope("write")           ◀── single dependency (FR-04)
                    │
                    ▼
              rate_limit_middleware             ◀── 429 + Retry-After (FR-05)
                    │
                    ▼
              TaskCreate (pydantic)              ◀── 422 on bad body (FR-10)
                    │
                    ▼
              service.tasks.create_task()
                    │
                    ▼
              repository.units_of_work.unit_of_work()   ◀── tx boundary (FR-06)
                    │
                    ▼
              repository.tasks.insert() + commit
                    │
                    ▼
              201 + TaskOut (problem_json never used)
```

### 3.3 Async Run Lifecycle (POST /v1/tasks/{id}/run — FR-02, FR-08)

```
client ──POST──▶ router (taskq.api.routes.runs)
                    │
                    ▼ require_scope("write")
                    │
                    ▼
              service.runner.enqueue(task_id)
                    │
                    ▼ asyncio.create_task
              TaskGroup ──bounded by TASKQ_MAX_CONCURRENT
                    │
                    ▼
              service.runner.run_subprocess(task)
                  ├─ shlex.split(command)
                  ├─ asyncio.create_subprocess_exec(*argv)  (shell=False — NFR-02)
                  ├─ asyncio.wait_for(..., timeout=TASKQ_TASK_TIMEOUT)
                  │     └─ on TimeoutError: process.kill(); await process.wait()
                  ├─ redact(stdout_tail); redact(stderr_tail)   (NFR-04)
                  └─ repository.results.insert(...)
                    │
                    ▼
              202 { run_id }   (status persisted; runs queryable via GET /runs)
```

### 3.4 Migration Round-Trip (FR-07 — load-bearing for verify-system)

```
make verify-system
  ├─ alembic upgrade head        # v1 → v2 → v3
  ├─ seed sample task (idempotent)
  ├─ alembic downgrade -1        # v3 → v2 — data moves BACK into tasks.result_json
  ├─ assert: row counts equal, column values equal (per-row)
  ├─ alembic upgrade head        # v2 → v3 — data moves FORWARD again
  └─ assert: identical values to seed step
```

### 3.5 External Interfaces (RFC 7807 Error Contract — FR-10)

| Status | `type` | Trigger |
|--------|--------|---------|
| 422 | `/errors/validation` | pydantic validation failure |
| 401 | `/errors/unauthenticated` | missing/invalid `X-API-Key` |
| 403 | `/errors/forbidden` | scope insufficient; **body never confirms existence** |
| 404 | `/errors/not-found` | unknown task id |
| 409 | `/errors/conflict` | duplicate `name` |
| 429 | `/errors/rate-limited` | bucket empty (`Retry-After` header set) |
| 503 | `/errors/not-ready` | DB unreachable OR `alembic current` ≠ head |
| 500 | `/errors/internal` | unexpected; `detail` sanitized |

---

## 4. NFR Handling

Every NFR (SPEC §4) is assigned a handling strategy and the modules/contracts that enforce it. The `dimension` column carries the verbatim value declared in `SRS.md` §4 (per `evaluate_dimension.md` §Step 1 vocabulary); the `type` column carries the SAB vocabulary from `core/quality_gate/sab_parser.ALL_NFR_TYPES`. They are two distinct vocabularies (SRS.md:771-776) and both must be carried downstream. `scope_layers` names the layers where the constraint's enforcement applies (omitted where the NFR is layer-agnostic). High-risk enforcement modules are marked **★**.

| NFR | dimension (SRS) | type (SAB) | scope_layers | Handling strategy | Enforcement site |
|-----|-----------------|-------------|--------------|-------------------|------------------|
| **NFR-01** Performance | `performance` | `performance` | repository | `selectinload`/`joinedload` mandatory for relationships; SQLAlchemy event listener asserts `≤4` statements at sizes {1,100,1000,10000}; pytest-benchmark measures p95 | `taskq.repository.tasks` ★, `taskq.repository.results` |
| **NFR-02** HTTP & data-layer security | `security` | `security` | — | `bandit -r src/` 0 HIGH/MEDIUM; grep gate for `shell=True`, `eval(`, `exec(`; CORS default-deny; SQL concat grep gate; 403 body leak guard | `taskq.api.middleware`, `taskq.service.auth` ★, `taskq.errors.handlers`, `taskq.service.runner` ★ |
| **NFR-03** Errors / tx / async correctness | `error_handling` | `reliability` | — | `unit_of_work()` ctx mgr enforces commit/rollback; AST check for bare `except` / `except Exception: pass`; `CancelledError` re-raised (never swallowed); `/readyz` fail-closed | `taskq.repository.units_of_work` ★, `taskq.service.runner` ★, `taskq.api.routes.health` |
| **NFR-04** Sensitive data redaction | `security` | `security` | — | Single `redact()` regex applied to `stdout_tail`/`stderr_tail`/logs/error bodies; `Settings.db_url` never logged; api-key plaintext printed only by `cli.key_create` | `taskq.errors.handlers` ★, `taskq.service.runner` ★, `taskq.cli.key_create` |
| **NFR-05** Documentation coverage | `documentation` | `documentation` | — | AST check: 100% of public functions/classes carry docstring with `[FR-XX]` or `[NFR-XX]` token; OpenAPI `summary`/`description` asserted via `/openapi.json` | `taskq.api.schemas`, all modules |
| **NFR-06** Layering contract | `architecture_constraints` | `layering` | ["api","service","cli","errors","config"] | `.importlinter` declares `api>service>repository>models` + forbidden `sqlalchemy` import outside `repository`/`models`; `lint-imports` must exit 0 | `taskq.config.settings` ★, layering enforced structurally |
| **NFR-07** Dependency & license compliance | `license_compliance` | `licensing` | — | `requirements.txt` pinned `==`; `requirements.lock` for transitive; `pip-licenses --format=json --with-system` matches allowlist {MIT, BSD-2/3, Apache-2.0, PSF}; SBOM emitted | `taskq.config.settings` (env-only) |
| **NFR-08** Mutation testing | `mutation_testing` | `mutation` | ["service","repository"] | `mutmut` scope limited to `taskq.service` + `taskq.repository` only (execution-time budget); mutation score ≥70 | `taskq.service.*`, `taskq.repository.*` ★ |
| **NFR-09** Zero-skip realism | `test_assertion_quality` | `testability` | — | `pytest -q` skipped == 0; every test ≥1 `assert`; `--ignore`/`-k`/`--deselect`/`collect_ignore` banned; FR-07 tested against **real SQLite file** | `taskq.repository.units_of_work` (real DB), `tests/integration/` |
| **NFR-10** Integration coverage | `integration_coverage` | `integration` | api, tests/integration | `httpx.AsyncClient(transport=ASGITransport(app))`; covers CRUD + every error code (401/403/404/409/422/429/503) + migration round-trip + rate-limit + drain; cov ≥80% on `tests/integration/` | `taskq.api.app` ★, `tests/integration/` |
| **NFR-11** Readability | `readability` | `maintainability` | — | MI ≥80 (LLOC-weighted); CC ≤10 per function; ≤400 lines/file; ≤15 files/dir; ≤40 lines per handler (business logic pushed down) | `taskq.service.tasks` (canonical small-module reference) |
| **NFR-12** System verification target | `execute_verification_target` | `verifiability` | cli, migrations | `Makefile` `verify-system` target: `alembic upgrade head` → pytest → uvicorn + ASGI smoke → `downgrade -1` → `upgrade head` → assert `verify-system: PASS` on stdout, exit 0 | `Makefile`, `taskq.cli.main`, `taskq.migrations.versions` ★ |

**NFR coverage (no NFR unmapped):** NFR-01..NFR-12 all addressed.

---

## 5. SAB Block (machine-readable — BINDING CONTRACT)

> **CONTRACT**: Field names, types, `sab:` root key, and `phase` as int must
> match `core/quality_gate/sab_parser.py:render_canonical_sab_template()`.
> Do NOT hand-write the YAML — paste from the canonical template and replace
> EXAMPLE values with your project's real values.
> Validate before committing: `python3 scripts/generate_sab.py --validate --project .`

<!-- SAB:START -->
```yaml
sab:
  version: "1.0"
  created_at: "2026-08-22"
  phase: 2  # MUST be int, NOT a string — parser raises on 'phase: "2"'
  project: "taskq-api"

  layers:
    - name: api
      modules:
        - name: "taskq.api.app"
        - name: "taskq.api.deps"
        - name: "taskq.api.middleware"
        - name: "taskq.api.schemas"
        - name: "taskq.api.routes.tasks"
        - name: "taskq.api.routes.runs"
        - name: "taskq.api.routes.keys"
        - name: "taskq.api.routes.health"
        - name: "taskq.api.routes.metrics"
      allowed_dependencies: ["service", "errors", "config"]
    - name: service
      modules:
        - name: "taskq.service.auth"
        - name: "taskq.service.tasks"
        - name: "taskq.service.rate_limit"
        - name: "taskq.service.runner"
        - name: "taskq.service.metrics"
      allowed_dependencies: ["repository", "errors", "config"]
    - name: repository
      modules:
        - name: "taskq.repository.units_of_work"
        - name: "taskq.repository.tasks"
        - name: "taskq.repository.keys"
        - name: "taskq.repository.results"
        - name: "taskq.repository.rate_buckets"
      allowed_dependencies: ["models", "errors", "config"]
    - name: models
      modules:
        - name: "taskq.models.base"
        - name: "taskq.models.task"
        - name: "taskq.models.api_key"
        - name: "taskq.models.task_result"
        - name: "taskq.models.tag"
        - name: "taskq.models.rate_bucket"
      allowed_dependencies: []
    - name: errors
      modules:
        - name: "taskq.errors.problem"
        - name: "taskq.errors.handlers"
      allowed_dependencies: []
    - name: config
      modules:
        - name: "taskq.config.settings"
        - name: "taskq.config.env"
      allowed_dependencies: []
    - name: cli
      modules:
        - name: "taskq.cli.main"
        - name: "taskq.cli.key_create"
      allowed_dependencies: ["service", "repository", "config"]

  allowed_dependencies:
    - { from: api,      to: service    }
    - { from: api,      to: errors     }
    - { from: api,      to: config     }
    - { from: service,  to: repository }
    - { from: service,  to: errors     }
    - { from: service,  to: config     }
    - { from: repository, to: models   }
    - { from: repository, to: errors   }
    - { from: repository, to: config   }
    - { from: cli,      to: service    }
    - { from: cli,      to: repository }
    - { from: cli,      to: config     }

  quality_targets:
    max_complexity: 15
    min_coverage: 100
    max_coupling: 0.3

  nfr_dimension_mapping: {}  # auto-derived from nfr_traceability.type

  nfr_traceability:
    NFR-01:
      type: performance
      dimension: performance
      target: "p95 <30ms single-get, p95 <80ms list; SQL stmt count <=4 across {1,100,1000,10000}"
      module: taskq.repository.tasks
    NFR-02:
      type: security
      dimension: security
      target: "0 shell=True/eval/exec; bandit 0 HIGH/MEDIUM; CORS default-deny; 0 SQL string-concat"
      module: taskq.api.middleware
    NFR-03:
      type: reliability
      dimension: error_handling
      target: "no bare except; CancelledError re-raised; graceful drain; /readyz fail-closed"
      module: taskq.service.runner
    NFR-04:
      type: security
      dimension: security
      target: "stdout/stderr/log redact regex hits; no DB URL in logs/metrics"
      module: taskq.errors.handlers
    NFR-05:
      type: documentation
      dimension: documentation
      target: "100% public fn/class docstring with [FR-XX]/[NFR-XX] token"
      module: taskq.api.schemas
    NFR-06:
      type: layering
      dimension: architecture_constraints
      target: "lint-imports exit 0; no sqlalchemy import outside repository/models"
      scope_layers: ["api", "service", "cli", "errors", "config"]   # layers where sqlalchemy import is forbidden (SRS.md:1009)
      module: taskq.config.settings
    NFR-07:
      type: licensing
      dimension: license_compliance
      target: "every dep in {MIT, BSD-2-Clause, BSD-3-Clause, Apache-2.0, PSF}; SBOM emitted"
      module: taskq.config.settings
    NFR-08:
      type: mutation
      dimension: mutation_testing
      target: ">=70 mutation score in service + repository only (time-budget)"
      scope_layers: ["service", "repository"]   # mutation scope per SRS.md:1072 (NFR-06 layer roles)
      module: taskq.service.tasks
    NFR-09:
      type: testability
      dimension: test_assertion_quality
      target: "0 skipped tests; >=1 assert per test; no --ignore/-k/--deselect/collect_ignore; FR-07 on real SQLite"
      module: taskq.repository.units_of_work
    NFR-10:
      type: integration
      dimension: integration_coverage
      target: "tests/integration cov >=80% via ASGITransport; every error code covered"
      module: taskq.api.app
    NFR-11:
      type: maintainability
      dimension: readability
      target: "MI >=80; CC <=10; file <=400 lines; dir <=15 files; handler <=40 lines"
      module: taskq.service.tasks
    NFR-12:
      type: verifiability
      dimension: execute_verification_target
      target: "make verify-system exit 0; stdout verify-system: PASS; round-trip migration verified"
      module: taskq.cli.main

  advisory_only: []  # AUTO-FILLED by parser — omit or leave []

  gate_score_overrides: {}  # AUTO-DERIVED by parser — omit or leave {}

  fr_module_traceability:
    FR-01: ["taskq.api.routes.tasks", "taskq.service.tasks", "taskq.repository.tasks", "taskq.api.schemas"]
    FR-02: ["taskq.api.routes.runs", "taskq.service.runner", "taskq.repository.results"]
    FR-03: ["taskq.cli.key_create", "taskq.service.auth", "taskq.repository.keys", "taskq.models.api_key"]
    FR-04: ["taskq.api.deps", "taskq.service.auth"]
    FR-05: ["taskq.api.middleware", "taskq.service.rate_limit", "taskq.repository.rate_buckets"]
    FR-06: ["taskq.repository.units_of_work", "taskq.repository.tasks"]
    FR-07: ["taskq.migrations.versions"]
    FR-08: ["taskq.service.runner"]
    FR-09: ["taskq.api.routes.health", "taskq.api.routes.metrics", "taskq.service.metrics"]
    FR-10: ["taskq.errors.problem", "taskq.errors.handlers", "taskq.api.middleware", "taskq.api.schemas"]

  architecture_constraints:
    - "no_circular_dependencies"
    - "layers api > service > repository > models (NFR-06)"
    - "config and errors are independence modules — no upward imports (NFR-06)"
    - "sqlalchemy import forbidden outside repository and models (NFR-06 forbidden contract)"
    - "no shell=True / eval / exec in src/ (NFR-02)"
    - "no SQL string concatenation in src/ (NFR-02)"
    - "CancelledError must propagate, never be swallowed (NFR-03)"

  high_risk_modules:
    - "taskq.service.runner"              # FR-08 async subprocess + timeout + drain
    - "taskq.api.deps"                    # FR-04 single auth/scope dependency
    - "taskq.repository.units_of_work"    # FR-06 transaction boundary
    - "taskq.migrations.versions"         # FR-07 v3 data migration (round-trip)
```
<!-- SAB:END -->

Note: Fill in the YAML above — it is used for Drift Detection and gate scoring.
Generate: `python3 scripts/generate_sab.py --project . [--overwrite]`

---

## 6. Security Design (STRIDE-lite — machine-readable, BINDING CONTRACT)

> **CONTRACT**: Field names and the `security_design:` root key are parsed
> by `core/quality_gate/security_design.py:extract_security_block()`.
> This block was generated from `render_canonical_security_template()` with
> EXAMPLE values replaced by real project values (10 threats across 4 trust
> boundaries, all STRIDE categories represented).
> Validate: `python3 harness_cli.py check-artifact-consistency --project .`

<!-- SEC:START -->
```yaml
security_design:
  version: "1.0"
  applicability: full   # taskq-api is an internet-facing HTTP service — full STRIDE-lite is required
  justification: ""
  trust_boundaries:
    - id: TB-01
      name: "external HTTP input"
      description: "unauthenticated clients crossing into taskq.api via /v1/* — X-API-Key is the only authentication"
    - id: TB-02
      name: "subprocess execution"
      description: "taskq.service.runner spawns asyncio subprocesses to execute task commands — host kernel boundary"
    - id: TB-03
      name: "database access"
      description: "taskq.repository reaches the SQL store via SQLAlchemy — service-layer code must never hold a Session directly"
    - id: TB-04
      name: "persisted secrets"
      description: "api_keys.key_hash storage and TASKQ_DB_URL handling in logs, error bodies, and /v1/metrics"
  threats:
    - id: T-01
      boundary: TB-01
      category: tampering
      description: "malformed request body mutates task state without schema validation"
      mitigation: "pydantic TaskCreate rejects unknown fields, length-blacklist, and non-string command — returns 422 + problem+json (FR-10)"
      owner_module: "taskq.api.routes.tasks"
      nfr: NFR-02
      verified_by: "test_sec_t01_malformed_payload_rejected"
    - id: T-02
      boundary: TB-01
      category: spoofing
      description: "forged X-API-Key bypasses authentication"
      mitigation: "sha256 lookup of key_hash + hmac.compare_digest + revoked_at IS NULL guard (FR-03)"
      owner_module: "taskq.service.auth"
      nfr: NFR-02
      verified_by: "test_sec_t02_invalid_api_key_rejected"
    - id: T-03
      boundary: TB-01
      category: elevation_of_privilege
      description: "write scope reaches admin-only endpoints (DELETE, /v1/metrics)"
      mitigation: "single require_scope() Depends() enforces read<write<admin before any resource lookup — 403 body cannot confirm existence (FR-04)"
      owner_module: "taskq.api.deps"
      nfr: NFR-02
      verified_by: "test_sec_t03_scope_escalation_blocked"
    - id: T-04
      boundary: TB-01
      category: denial_of_service
      description: "unbounded request volume exhausts backend resources"
      mitigation: "per-token token bucket (TASKQ_RATE_BURST / TASKQ_RATE_PER_SEC) consumed in row-locked tx; 429 + Retry-After header (FR-05)"
      owner_module: "taskq.api.middleware"
      nfr: NFR-02
      verified_by: "test_sec_t04_rate_limit_returns_429"
    - id: T-05
      boundary: TB-02
      category: elevation_of_privilege
      description: "shell=True or eval()/exec() grants attacker arbitrary command execution"
      mitigation: "asyncio.create_subprocess_exec(*shlex.split(cmd)) with shell=False; repo-wide grep gate for shell=True/eval(/exec( (NFR-02)"
      owner_module: "taskq.service.runner"
      nfr: NFR-02
      verified_by: "test_sec_t05_runner_rejects_shell_true"
    - id: T-06
      boundary: TB-02
      category: denial_of_service
      description: "task timeout leaves orphan subprocess consuming resources"
      mitigation: "asyncio.wait_for triggers process.kill() + await process.wait() before raising (FR-08 / NFR-03)"
      owner_module: "taskq.service.runner"
      nfr: NFR-03
      verified_by: "test_sec_t06_timeout_kills_subprocess"
    - id: T-07
      boundary: TB-03
      category: tampering
      description: "SQL string concatenation enables injection"
      mitigation: "ORM / parameterized queries only; grep CI gate for f-string/%/+ SQL composition (NFR-02)"
      owner_module: "taskq.repository.tasks"
      nfr: NFR-02
      verified_by: "test_sec_t07_sql_injection_blocked"
    - id: T-08
      boundary: TB-03
      category: information_disclosure
      description: "TASKQ_DB_URL with password leaks via error body, log line, or /v1/metrics"
      mitigation: "taskq.errors.handlers.problem_json enforces detail whitelist; taskq.config.settings never logs db_url (NFR-04)"
      owner_module: "taskq.errors.handlers"
      nfr: NFR-04
      verified_by: "test_sec_t08_db_url_redacted_in_logs"
    - id: T-09
      boundary: TB-04
      category: repudiation
      description: "api_keys row stores plaintext secret enabling undetectable key compromise"
      mitigation: "taskq.service.auth hashes via sha256 before insert; plaintext printed only by taskq.cli.key_create at creation time (NFR-02 / FR-03)"
      owner_module: "taskq.service.auth"
      nfr: NFR-02
      verified_by: "test_sec_t09_api_key_hashed_in_storage"
    - id: T-10
      boundary: TB-04
      category: information_disclosure
      description: "stdout_tail/stderr_tail/log lines leak sk-/Bearer/token patterns or postgres:// URLs"
      mitigation: "taskq.errors.handlers.redact() applies NFR-04 regex line-level before persistence and logging (NFR-04)"
      owner_module: "taskq.errors.handlers"
      nfr: NFR-04
      verified_by: "test_sec_t10_subprocess_output_redacted"
```
<!-- SEC:END -->

Note: `owner_module` names a module declared in the §5 SAB block;
`nfr` references an NFR present in `SPEC.md` §4; `verified_by` names the
test that proves the mitigation — from Phase 5 onward, `check-artifact-consistency`
blocks if that test doesn't exist yet. Threats also seed
`bug-hunt-targets`' adversarial-review targeting and force NFR-pattern
test cases in `derive_test_cases.md` Step 1c regardless of SRS keywords.

---

## 7. Architectural Non-Goals

- **No multi-tenant key scoping** — scope is per-token only (SPEC FR-04).
- **No message broker / queue** — task execution is in-process via `asyncio.TaskGroup` (SPEC FR-08).
- **No GraphQL / gRPC** — REST only (SPEC §2).
- **No ORM outside `repository` + `models`** — `sqlalchemy` import is structurally forbidden (SPEC NFR-06).
- **No string SQL anywhere** — ORM / parameterized only (SPEC NFR-02).

---

## 8. Open Architectural Decisions

| ID | Decision | Status |
|----|----------|--------|
| A-01 | `cli/` is a separate layer (allowed_dependencies: service, repository, config) rather than inside `api/` — keeps operational surface decoupled from HTTP entry | decided |
| A-02 | `migrations/` lives under `src/taskq/` rather than project root — keeps migration env.py co-located with models it imports | decided |
| A-03 | Each `api/routes/<feature>.py` file mirrors exactly one resource family (tasks/runs/keys/health/metrics) — no combined `routes/api.py` god-module | decided |

---

*Phase 2 deliverable. Updates to this document must propagate to the §5 SAB block and §6 SEC block; the orchestrator's `check-artifact-consistency` enforces cross-reference invariants.*
