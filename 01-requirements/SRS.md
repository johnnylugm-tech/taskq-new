# Software Requirements Specification (SRS) — taskq-api

> **Phase**: 1 — Requirements | **Mode**: INGESTION (canonical = SPEC.md v1.0.0)
> **Project**: taskq-api — HTTP task-queue service (Python 3.11 / FastAPI / SQLAlchemy / Alembic)
> **Source of truth**: `SPEC.md` (v1.0.0, 2026-07-30) at project root.
> This SRS is a 100% verbatim transcription of the 10 FR / 12 NFR clauses
> from §3 and §4 of `SPEC.md`, expanded into machine-citable ACs (§3-§4 of
> this file) plus acceptance-criteria summary (§5), out-of-scope (§6),
> open issues (§7), risks (§8), glossary (§9), and the FR Block (§10)
> downstream consumers require.

---

## 1. Introduction

### 1.1 Purpose

`taskq-api` is an HTTP task-queue service that exposes a REST API to submit,
query, and execute shell-command tasks; persists state in a relational
database through SQLAlchemy; evolves the schema with Alembic;
authenticates clients via hashed API keys; authorises by scope; and
throttles per token. (Source: SPEC.md §1 — 概述, §0 — 設計意圖.)

### 1.2 Scope

In scope (FR-01..FR-10, NFR-01..NFR-12):
- HTTP boundary: REST endpoints under `/v1/*`, request validation, scope
  authorization, rate-limiting, RFC 7807 error contract
- Real database: SQLAlchemy 2.x ORM, explicit `Session` transaction
  boundaries, Alembic-driven schema evolution (three revisions, one
  data-moving, every step reversible)
- Async execution: `asyncio.create_subprocess_exec` task runner
  (`shell=True` forbidden everywhere), graceful drain on shutdown
- Operational endpoints: `/healthz`, `/readyz` (fail-closed on migration
  lag), `/v1/metrics`
- Quality gates covering `performance` / `security` / `error_handling` /
  `documentation` / `architecture_constraints` / `license_compliance` /
  `mutation_testing` / `test_assertion_quality` / `integration_coverage` /
  `readability` / `execute_verification_target`

Out of scope (SPEC.md §0): schema-folder layout, module path of
sub-process runner / auth dependency / transaction context manager / v3
data-migration revision file — these are owned by the framework per the
canonical rule "角色不變,路徑變".

### 1.3 Definitions and Acronyms

See §9 Glossary.

### 1.4 References

| ID | Document | Version | Date |
|----|----------|---------|------|
| SPEC | `SPEC.md` at project root | v1.0.0 | 2026-07-30 |
| BRIEF | `PROJECT_BRIEF.md` at project root | round-2 | 2026-08-22 |
| RFC 7807 | "Problem Details for HTTP APIs" | RFC 7807 | 2016-03 |
| NFR-type vocabulary | `harness/core/quality_gate/sab_parser.ALL_NFR_TYPES` | pinned | — |

### 1.5 Document Structure

- §2 Constraints (technical / architecture / security / migration /
  async / query / readiness / verification)
- §3 Functional Requirements (FR-01..FR-10, one section each, with
  `#### AC-x.y` headings)
- §4 Non-Functional Requirements (NFR-01..NFR-12, one section each, with
  `#### AC-Nx.y` headings)
- §5 Acceptance Criteria Summary (SPEC §8 + §11)
- §6 Out-of-Scope
- §7 Open Issues (deferred items)
- §8 Risks
- §9 Glossary
- §10 FR Block (machine-readable)

---

## 2. Constraints

These constraints govern every FR / NFR below. They are transcribed
verbatim from `SPEC.md` §0 / §2 / §5.3 / §10 (round-2 design intent) and
are **not** invented.

### 2.1 Technical

- **Language**: Python 3.11 (SPEC.md §1 概述)
- **HTTP framework**: FastAPI (ASGI) (SPEC.md §2 技術架構)
- **Data validation**: `pydantic` v2 request/response models (SPEC.md §2)
- **ORM**: SQLAlchemy 2.x (declarative + `Session` with explicit
  transaction boundaries) (SPEC.md §2)
- **Databases**: SQLite (dev / test), PostgreSQL (production), same ORM
  models (SPEC.md §2)
- **Migration**: Alembic with three revisions (v1 → v2 → v3), every step
  has a working `downgrade` (SPEC.md §2, §3 FR-07, §5.3)
- **Async**: `async def` endpoints + `asyncio.TaskGroup` background
  runner (SPEC.md §2)
- **Subprocess**: `asyncio.create_subprocess_exec`; `shell=True`
  forbidden everywhere (SPEC.md §2, §3 FR-02, NFR-02)

### 2.2 Architecture

- Four layers `api > service > repository > models` enforced by a
  mandatory `.importlinter` contract (SPEC.md §3 FR-06, NFR-06).
- `config` and `errors` are independence modules (SPEC.md §4 NFR-06).
- `sqlalchemy` may only be imported by `repository/`; ORM leakage into
  the business layer is the specific anti-pattern this round guards
  against (SPEC.md §4 NFR-06, §10 framework 對齊).
- Layer roles (not paths) are specified; concrete module paths and file
  names are decided by the framework (SPEC.md §5.3 note, §10 高風險模組).

### 2.3 Security

- API keys stored as SHA-256 hashes and compared with
  `hmac.compare_digest` (SPEC.md §3 FR-03, NFR-02)
- 403 responses must not reveal whether the resource exists (SPEC.md §3
  FR-04)
- No string-concatenated SQL anywhere (SPEC.md §3 FR-06, NFR-02)
- CORS denies all origins by default; `TASKQ_CORS_ORIGINS` is the
  allowlist (SPEC.md §4 NFR-02, §5.1)
- Error bodies must not carry stack traces, SQL or file paths (SPEC.md
  §3 FR-10, NFR-02)
- Sensitive data redaction before write / emit (SPEC.md §4 NFR-04)

### 2.4 Migration

- Three revisions: v1 base tables, v2 tags many-to-many, v3 moves
  `tasks.result_json` to `task_results` with real data migration
  (SPEC.md §0 本輪設計意圖, §3 FR-07)
- `upgrade head` → sample write → `downgrade -1` → `upgrade head` must
  leave every column byte-identical (SPEC.md §3 FR-07, §8 #12)
- `alembic downgrade base` must leave no residual tables (SPEC.md §8 #13)
- Migration tested against a real SQLite file, never an in-memory mock
  (SPEC.md §4 NFR-09 本輪特別條款)

### 2.5 Async correctness

- `asyncio.CancelledError` must propagate; it must never be swallowed
  by `except Exception` (SPEC.md §3 FR-08, NFR-03)
- Task timeouts must actually kill the child process (`kill()` then
  `await wait()`), leaving no orphans (SPEC.md §3 FR-08)
- Shutdown drains in-flight work up to `TASKQ_DRAIN_TIMEOUT` (SPEC.md
  §3 FR-08, §5.1)

### 2.6 Query efficiency

- Relationship loads must be explicit (`selectinload` / `joinedload`)
  (SPEC.md §3 FR-06)
- **N+1 is an acceptance failure** — the list endpoint's SQL statement
  count must be constant regardless of how many rows come back (SPEC.md
  §3 FR-06, NFR-01, §8 #14)

### 2.7 Readiness

- `/readyz` returns 503 when the database is unreachable **or** when
  `alembic current` is not at head — deploying new code without running
  the migration must fail closed (SPEC.md §3 FR-09, §8 #11)

### 2.8 Verification honesty

- Same zero-skip rule as round 1 (SPEC.md §4 NFR-09)
- Three-step migration must be tested against a real database file, not
  a mock; may not be downgraded to a skip on the grounds that "migration
  logic is hard to test" (SPEC.md §4 NFR-09)
- `crg_cohesion_healthy` keeps its default value, never lowered to make
  the project pass (SPEC.md §10 framework 對齊 CRG 校準鐵律)

---

## 3. Functional Requirements

> Each FR section is headed `### FR-XX: <canonical title>`. Acceptance
> criteria carry stable `#### AC-x.y` identifiers (machine-citable by
> Phase 2 / Phase 3 / Phase 5 artefacts).

### FR-01: 任務資源 CRUD API

> Source: SPEC.md §3 FR-01. Scope: 4 endpoints under `/v1/tasks`. <!-- DERIVED: SPEC.md §3 — English rendering of canonical Chinese clause; measurement boundary owned by test harness per canonical line. -->

| Method | Path | Scope | Behaviour |
|--------|------|-------|-----------|
| POST | `/v1/tasks` | `write` | 建立任務;body 由 `TaskCreate` pydantic 模型驗證 |
| GET | `/v1/tasks/{id}` | `read` | 取得單一任務全欄位 |
| GET | `/v1/tasks` | `read` | 分頁列表,支援 `?status=`、`?limit=`、`?cursor=` |
| DELETE | `/v1/tasks/{id}` | `admin` | 刪除任務(連同結果列,同一交易) |

**Behavioural rules** (SPEC.md §3 FR-01, §7, §8):
- 驗證規則:**request body 驗證**(非空 / ≤1000 字元 / 注入字元黑名單)
  違反 → **HTTP 422** + problem+json
- **名稱唯一性違反 → HTTP 409** + problem+json (uniqueness 為資料層
  約束,非 request body 驗證;對齊 §8 #8 與 HTTP Status Map 的 409 行為)
- 未知 id → **HTTP 404** + problem+json
- 分頁為 **cursor-based**(不得用 offset —— 大表 offset 掃描是 N+1 的
  親戚)
- 列表端點的預設 `limit` 為 50,上限 200;超過上限 → 422

#### AC-1.1
<!-- DERIVED: SPEC.md §3 FR-1 — English rendering of canonical Chinese clause; measurement boundary owned by test harness per canonical line. -->

<!-- DERIVED: SPEC.md §3 FR-1 — English rendering of canonical Chinese clause; measurement boundary owned by test harness per canonical line. -->

`POST /v1/tasks` with a valid `write`-scoped API key and a body
satisfying the validation rules (non-empty, ≤1000 chars, no injection
blacklist characters) returns **HTTP 201** with a `task id` in the
response body (SPEC.md §8 #4).

#### AC-1.2
<!-- DERIVED: SPEC.md §3 FR-1 — English rendering of canonical Chinese clause; measurement boundary owned by test harness per canonical line. -->

<!-- DERIVED: SPEC.md §3 FR-1 — English rendering of canonical Chinese clause; measurement boundary owned by test harness per canonical line. -->

`POST /v1/tasks` without an `X-API-Key` header returns **HTTP 401** +
`application/problem+json` (SPEC.md §8 #5).

#### AC-1.3
<!-- DERIVED: SPEC.md §3 FR-1 — English rendering of canonical Chinese clause; measurement boundary owned by test harness per canonical line. -->

<!-- DERIVED: SPEC.md §3 FR-1 — English rendering of canonical Chinese clause; measurement boundary owned by test harness per canonical line. -->

`POST /v1/tasks` with a body violating validation rules (empty body,
>1000 chars, or contains a blacklisted injection character) returns
**HTTP 422** + `application/problem+json` (SPEC.md §3 FR-01, §7).

#### AC-1.4
<!-- DERIVED: SPEC.md §3 FR-1 — English rendering of canonical Chinese clause; measurement boundary owned by test harness per canonical line. -->

<!-- DERIVED: SPEC.md §3 FR-1 — English rendering of canonical Chinese clause; measurement boundary owned by test harness per canonical line. -->

`POST /v1/tasks` with a name that already exists returns **HTTP 409** +
`application/problem+json` (SPEC.md §3 FR-01, §8 #8).

#### AC-1.5
<!-- DERIVED: SPEC.md §3 FR-1 — English rendering of canonical Chinese clause; measurement boundary owned by test harness per canonical line. -->

<!-- DERIVED: SPEC.md §3 FR-1 — English rendering of canonical Chinese clause; measurement boundary owned by test harness per canonical line. -->

`GET /v1/tasks/{id}` for an existing id returns **HTTP 200** with the
task's full fields (SPEC.md §3 FR-01).

#### AC-1.6
<!-- DERIVED: SPEC.md §3 FR-1 — English rendering of canonical Chinese clause; measurement boundary owned by test harness per canonical line. -->

<!-- DERIVED: SPEC.md §3 FR-1 — English rendering of canonical Chinese clause; measurement boundary owned by test harness per canonical line. -->

`GET /v1/tasks/{unknown}` returns **HTTP 404** + `application/problem+json`
(SPEC.md §3 FR-01, §8 #7).

#### AC-1.7
<!-- DERIVED: SPEC.md §3 FR-1 — English rendering of canonical Chinese clause; measurement boundary owned by test harness per canonical line. -->

<!-- DERIVED: SPEC.md §3 FR-1 — English rendering of canonical Chinese clause; measurement boundary owned by test harness per canonical line. -->

`GET /v1/tasks` supports `?status=`, `?limit=`, `?cursor=`; pagination
is cursor-based (NOT offset-based); default `limit` is 50, max is 200
(SPEC.md §3 FR-01).

#### AC-1.8
<!-- DERIVED: SPEC.md §3 FR-1 — English rendering of canonical Chinese clause; measurement boundary owned by test harness per canonical line. -->

<!-- DERIVED: SPEC.md §3 FR-1 — English rendering of canonical Chinese clause; measurement boundary owned by test harness per canonical line. -->

`GET /v1/tasks?limit=201` (or any limit > 200) returns **HTTP 422** +
`application/problem+json` (SPEC.md §3 FR-01).

#### AC-1.9
<!-- DERIVED: SPEC.md §3 FR-1 — English rendering of canonical Chinese clause; measurement boundary owned by test harness per canonical line. -->

<!-- DERIVED: SPEC.md §3 FR-1 — English rendering of canonical Chinese clause; measurement boundary owned by test harness per canonical line. -->

`DELETE /v1/tasks/{id}` with a `write`-scoped (non-`admin`) key returns
**HTTP 403**, and the response body does not reveal whether the id
exists (SPEC.md §3 FR-04, §8 #6).

#### AC-1.10
<!-- DERIVED: SPEC.md §3 FR-1 — English rendering of canonical Chinese clause; measurement boundary owned by test harness per canonical line. -->

<!-- DERIVED: SPEC.md §3 FR-1 — English rendering of canonical Chinese clause; measurement boundary owned by test harness per canonical line. -->

`DELETE /v1/tasks/{id}` with an `admin`-scoped key removes the task row
and its associated result rows in the same transaction (SPEC.md §3
FR-01, FR-06).

### FR-02: 任務執行端點

> Source: SPEC.md §3 FR-02. <!-- DERIVED: SPEC.md §3 — English rendering of canonical Chinese clause; measurement boundary owned by test harness per canonical line. -->

- `POST /v1/tasks/{id}/run`(scope `write`)→ **HTTP 202 Accepted**,body
  含 `run_id`
- 實際執行以 `asyncio.create_subprocess_exec(*shlex.split(command))`
  進行,**禁 `shell=True`**,timeout 為 `TASKQ_TASK_TIMEOUT`
- 狀態機:`pending → running → done | failed | timeout`
- 執行結果寫入 `task_results` 表(FR-07 的 v3 schema),欄位:
  `exit_code` / `stdout_tail` / `stderr_tail` / `duration_ms` /
  `finished_at`
- `GET /v1/tasks/{id}/runs`(scope `read`)→ 該任務的歷史執行紀錄,新到
  舊排序

#### AC-2.1
<!-- DERIVED: SPEC.md §3 FR-2 — English rendering of canonical Chinese clause; measurement boundary owned by test harness per canonical line. -->

<!-- DERIVED: SPEC.md §3 FR-2 — English rendering of canonical Chinese clause; measurement boundary owned by test harness per canonical line. -->

`POST /v1/tasks/{id}/run` returns **HTTP 202** with a `run_id` in the
body (SPEC.md §3 FR-02).

#### AC-2.2
<!-- DERIVED: SPEC.md §3 FR-2 — English rendering of canonical Chinese clause; measurement boundary owned by test harness per canonical line. -->

<!-- DERIVED: SPEC.md §3 FR-2 — English rendering of canonical Chinese clause; measurement boundary owned by test harness per canonical line. -->

Subprocess execution uses
`asyncio.create_subprocess_exec(*shlex.split(command))`; `shell=True`
does not appear anywhere in `src/` (SPEC.md §3 FR-02, NFR-02, §8 #16).

#### AC-2.3
<!-- DERIVED: SPEC.md §3 FR-2 — English rendering of canonical Chinese clause; measurement boundary owned by test harness per canonical line. -->

<!-- DERIVED: SPEC.md §3 FR-2 — English rendering of canonical Chinese clause; measurement boundary owned by test harness per canonical line. -->

Task state machine is `pending → running → done | failed | timeout`;
each terminal state is reachable from `running` via a real subprocess
exit (SPEC.md §3 FR-02).

#### AC-2.4
<!-- DERIVED: SPEC.md §3 FR-2 — English rendering of canonical Chinese clause; measurement boundary owned by test harness per canonical line. -->

<!-- DERIVED: SPEC.md §3 FR-2 — English rendering of canonical Chinese clause; measurement boundary owned by test harness per canonical line. -->

Execution result is persisted to `task_results` with columns
`exit_code`, `stdout_tail`, `stderr_tail`, `duration_ms`, `finished_at`
(SPEC.md §3 FR-02, §5.2).

#### AC-2.5
<!-- DERIVED: SPEC.md §3 FR-2 — English rendering of canonical Chinese clause; measurement boundary owned by test harness per canonical line. -->

<!-- DERIVED: SPEC.md §3 FR-2 — English rendering of canonical Chinese clause; measurement boundary owned by test harness per canonical line. -->

`GET /v1/tasks/{id}/runs` returns the task's execution history sorted
newest-first (SPEC.md §3 FR-02).

### FR-03: API Key 認證

> Source: SPEC.md §3 FR-03. <!-- DERIVED: SPEC.md §3 — English rendering of canonical Chinese clause; measurement boundary owned by test harness per canonical line. -->

- 全部 `/v1/*` 端點要求 `X-API-Key` header;缺少或無效 → **HTTP 401** +
  problem+json
- 金鑰**以 SHA-256 雜湊儲存**於 `api_keys` 表,**不得存明文**;比對用
  `hmac.compare_digest`(常數時間)
- 金鑰由框架決定的 CLI 介面產生(例如
  `python -m <module> key create --scope <scope>`);明文**只在建立當下
  印出一次**
- 停用金鑰:`revoked_at` 非空的金鑰一律視為無效
- `/healthz`、`/readyz` 不要求認證(FR-09)

#### AC-3.1
<!-- DERIVED: SPEC.md §3 FR-3 — English rendering of canonical Chinese clause; measurement boundary owned by test harness per canonical line. -->

<!-- DERIVED: SPEC.md §3 FR-3 — English rendering of canonical Chinese clause; measurement boundary owned by test harness per canonical line. -->

Every `/v1/*` endpoint requires `X-API-Key`; a missing or invalid key
returns **HTTP 401** + `application/problem+json` (SPEC.md §3 FR-03,
§7, §8 #5).

#### AC-3.2
<!-- DERIVED: SPEC.md §3 FR-3 — English rendering of canonical Chinese clause; measurement boundary owned by test harness per canonical line. -->

<!-- DERIVED: SPEC.md §3 FR-3 — English rendering of canonical Chinese clause; measurement boundary owned by test harness per canonical line. -->

API keys are stored as SHA-256 hashes in `api_keys.key_hash`; the table
holds no plaintext keys, and `key_hash` is exactly 64 hex characters
(SPEC.md §3 FR-03, §8 #18).

#### AC-3.3
<!-- DERIVED: SPEC.md §3 FR-3 — English rendering of canonical Chinese clause; measurement boundary owned by test harness per canonical line. -->

<!-- DERIVED: SPEC.md §3 FR-3 — English rendering of canonical Chinese clause; measurement boundary owned by test harness per canonical line. -->

Key comparison uses `hmac.compare_digest` (constant-time) (SPEC.md §3
FR-03, NFR-02).

#### AC-3.4
<!-- DERIVED: SPEC.md §3 FR-3 — English rendering of canonical Chinese clause; measurement boundary owned by test harness per canonical line. -->

<!-- DERIVED: SPEC.md §3 FR-3 — English rendering of canonical Chinese clause; measurement boundary owned by test harness per canonical line. -->

Key creation prints the plaintext to stdout exactly once and never
persists it; the plaintext appears in no log, error body, or `/v1/metrics`
response (SPEC.md §3 FR-03, NFR-04).

#### AC-3.5
<!-- DERIVED: SPEC.md §3 FR-3 — English rendering of canonical Chinese clause; measurement boundary owned by test harness per canonical line. -->

<!-- DERIVED: SPEC.md §3 FR-3 — English rendering of canonical Chinese clause; measurement boundary owned by test harness per canonical line. -->

A key with non-null `revoked_at` is treated as invalid (SPEC.md §3
FR-03).

#### AC-3.6
<!-- DERIVED: SPEC.md §3 FR-3 — English rendering of canonical Chinese clause; measurement boundary owned by test harness per canonical line. -->

<!-- DERIVED: SPEC.md §3 FR-3 — English rendering of canonical Chinese clause; measurement boundary owned by test harness per canonical line. -->

`/healthz` and `/readyz` do not require authentication (SPEC.md §3
FR-03, FR-09).

### FR-04: Scope 授權

> Source: SPEC.md §3 FR-04. <!-- DERIVED: SPEC.md §3 — English rendering of canonical Chinese clause; measurement boundary owned by test harness per canonical line. -->

- 每把金鑰帶一個 scope:`read` < `write` < `admin`(階層包含)
- 端點所需 scope 見 FR-01/02 表;不足 → **HTTP 403** + problem+json,且
  **body 不得洩漏該資源是否存在**
- 授權判定必須在**單一中介層(dependency)**完成,不得散落於各 handler
  —— 以測試斷言「每個 `/v1` 路由都經過同一個 dependency」

#### AC-4.1
<!-- DERIVED: SPEC.md §3 FR-4 — English rendering of canonical Chinese clause; measurement boundary owned by test harness per canonical line. -->

<!-- DERIVED: SPEC.md §3 FR-4 — English rendering of canonical Chinese clause; measurement boundary owned by test harness per canonical line. -->

Each key carries a scope from `{read, write, admin}` with hierarchical
containment `read < write < admin` (SPEC.md §3 FR-04).

#### AC-4.2
<!-- DERIVED: SPEC.md §3 FR-4 — English rendering of canonical Chinese clause; measurement boundary owned by test harness per canonical line. -->

<!-- DERIVED: SPEC.md §3 FR-4 — English rendering of canonical Chinese clause; measurement boundary owned by test harness per canonical line. -->

A request to an endpoint with insufficient scope returns **HTTP 403** +
`application/problem+json`; the body does not reveal whether the
requested resource exists (SPEC.md §3 FR-04, §8 #6).

#### AC-4.3
<!-- DERIVED: SPEC.md §3 FR-4 — English rendering of canonical Chinese clause; measurement boundary owned by test harness per canonical line. -->

<!-- DERIVED: SPEC.md §3 FR-4 — English rendering of canonical Chinese clause; measurement boundary owned by test harness per canonical line. -->

Authorization is enforced by exactly one FastAPI dependency; a test
asserts every `/v1` route passes through this single dependency
(SPEC.md §3 FR-04).

### FR-05: 流量控制

> Source: SPEC.md §3 FR-05. <!-- DERIVED: SPEC.md §3 — English rendering of canonical Chinese clause; measurement boundary owned by test harness per canonical line. -->

- per-token 令牌桶:容量 `TASKQ_RATE_BURST`,補充速率 `TASKQ_RATE_PER_SEC`
- 超限 → **HTTP 429** + problem+json + `Retry-After` header(秒)
- 令牌桶狀態存於資料庫(跨 worker 一致),更新必須在單一交易內以
  row-level lock 進行
- `/healthz`、`/readyz` 不受限

#### AC-5.1
<!-- DERIVED: SPEC.md §3 FR-5 — English rendering of canonical Chinese clause; measurement boundary owned by test harness per canonical line. -->

<!-- DERIVED: SPEC.md §3 FR-5 — English rendering of canonical Chinese clause; measurement boundary owned by test harness per canonical line. -->

Per-token token bucket has capacity `TASKQ_RATE_BURST` and refill rate
`TASKQ_RATE_PER_SEC` (SPEC.md §3 FR-05, §5.1).

#### AC-5.2
<!-- DERIVED: SPEC.md §3 FR-5 — English rendering of canonical Chinese clause; measurement boundary owned by test harness per canonical line. -->

<!-- DERIVED: SPEC.md §3 FR-5 — English rendering of canonical Chinese clause; measurement boundary owned by test harness per canonical line. -->

A request that exceeds the bucket returns **HTTP 429** +
`application/problem+json` with a `Retry-After` header whose value is
in seconds (SPEC.md §3 FR-05, §8 #9).

#### AC-5.3
<!-- DERIVED: SPEC.md §3 FR-5 — English rendering of canonical Chinese clause; measurement boundary owned by test harness per canonical line. -->

<!-- DERIVED: SPEC.md §3 FR-5 — English rendering of canonical Chinese clause; measurement boundary owned by test harness per canonical line. -->

Token-bucket state is stored in the database (consistent across
workers); updates run inside a single transaction with row-level lock
(SPEC.md §3 FR-05).

#### AC-5.4
<!-- DERIVED: SPEC.md §3 FR-5 — English rendering of canonical Chinese clause; measurement boundary owned by test harness per canonical line. -->

<!-- DERIVED: SPEC.md §3 FR-5 — English rendering of canonical Chinese clause; measurement boundary owned by test harness per canonical line. -->

`/healthz` and `/readyz` are not subject to rate limiting (SPEC.md §3
FR-05, FR-09).

### FR-06: 持久化層與交易邊界

> Source: SPEC.md §3 FR-06. <!-- DERIVED: SPEC.md §3 — English rendering of canonical Chinese clause; measurement boundary owned by test harness per canonical line. -->

- 全部資料存取經由**資料存取層**(`repository` layer per NFR-06),
  **業務層不得直接持有 `Session`**;具體目錄與 module path 由框架決定
- 每個 API 請求一個 `Session`,交易邊界明確:成功 commit、例外
  rollback(以 context manager 保證)
- **禁止字串拼接 SQL**;一律使用 ORM 或參數化查詢(NFR-02)
- 關聯查詢必須用 `selectinload` / `joinedload` 顯式預載 —— **N+1 為
  驗收失敗條件**(NFR-01)
- 連線池:`pool_size=TASKQ_DB_POOL_SIZE`,`pool_pre_ping=True`

#### AC-6.1
<!-- DERIVED: SPEC.md §3 FR-6 — English rendering of canonical Chinese clause; measurement boundary owned by test harness per canonical line. -->

<!-- DERIVED: SPEC.md §3 FR-6 — English rendering of canonical Chinese clause; measurement boundary owned by test harness per canonical line. -->

All data access goes through the repository layer; the business
(service) layer does not import or hold a SQLAlchemy `Session` (SPEC.md
§3 FR-06, NFR-06).

#### AC-6.2
<!-- DERIVED: SPEC.md §3 FR-6 — English rendering of canonical Chinese clause; measurement boundary owned by test harness per canonical line. -->

<!-- DERIVED: SPEC.md §3 FR-6 — English rendering of canonical Chinese clause; measurement boundary owned by test harness per canonical line. -->

Each API request gets exactly one `Session`; the transaction boundary
is enforced by a context manager that commits on success and rolls
back on exception (SPEC.md §3 FR-06).

#### AC-6.3
<!-- DERIVED: SPEC.md §3 FR-6 — English rendering of canonical Chinese clause; measurement boundary owned by test harness per canonical line. -->

<!-- DERIVED: SPEC.md §3 FR-6 — English rendering of canonical Chinese clause; measurement boundary owned by test harness per canonical line. -->

No string-concatenated SQL appears in `src/`; a grep gate for
f-string / `%` / `+` SQL composition reports **0 hits** (SPEC.md §3
FR-06, NFR-02, §8 #17).

#### AC-6.4
<!-- DERIVED: SPEC.md §3 FR-6 — English rendering of canonical Chinese clause; measurement boundary owned by test harness per canonical line. -->

<!-- DERIVED: SPEC.md §3 FR-6 — English rendering of canonical Chinese clause; measurement boundary owned by test harness per canonical line. -->

Relationship loads use `selectinload` / `joinedload` explicitly; the
list endpoint's SQL statement count is constant (independent of row
count) and ≤ 4 (SPEC.md §3 FR-06, NFR-01, §8 #14).

#### AC-6.5
<!-- DERIVED: SPEC.md §3 FR-6 — English rendering of canonical Chinese clause; measurement boundary owned by test harness per canonical line. -->

<!-- DERIVED: SPEC.md §3 FR-6 — English rendering of canonical Chinese clause; measurement boundary owned by test harness per canonical line. -->

Connection pool uses `pool_size=TASKQ_DB_POOL_SIZE` and
`pool_pre_ping=True` (SPEC.md §3 FR-06).

### FR-07: Schema Migration(Alembic 三步演進)

> Source: SPEC.md §3 FR-07. Three revisions; every step reversible. <!-- DERIVED: SPEC.md §3 — English rendering of canonical Chinese clause; measurement boundary owned by test harness per canonical line. -->

| revision | upgrade 內容 | downgrade 要求 |
|---|---|---|
| **v1** | 建立 `tasks`、`api_keys` 兩表 | drop 兩表 |
| **v2** | 新增 `tags`、`task_tags`(多對多)+ `tasks.name` 唯一索引 | drop 新表與索引,不影響 v1 資料 |
| **v3** | **含資料搬遷**:把 `tasks.result_json` 拆為獨立的 `task_results` 表,搬遷既有資料後移除原欄位 | 反向搬遷回 `tasks.result_json` 後 drop `task_results`,**資料不得遺失** |

- `alembic upgrade head` 與 `alembic downgrade base` 必須都成功
- **往返可逆性驗收**:`upgrade head` → 寫入樣本資料 → `downgrade -1`
  → `upgrade head`,樣本資料的欄位值必須逐欄相同(v3 的資料搬遷是
  本條的重點)
- 禁止以 `op.execute("DROP TABLE ...")` 之類的破壞性捷徑取代真正的
  downgrade
- migration 檔本身納入測試覆蓋(以 `alembic` 的 offline SQL 產生 +
  斷言)

#### AC-7.1
<!-- DERIVED: SPEC.md §3 FR-7 — English rendering of canonical Chinese clause; measurement boundary owned by test harness per canonical line. -->

<!-- DERIVED: SPEC.md §3 FR-7 — English rendering of canonical Chinese clause; measurement boundary owned by test harness per canonical line. -->

Revision v1 creates the `tasks` and `api_keys` tables; downgrade drops
both tables (SPEC.md §3 FR-07).

#### AC-7.2
<!-- DERIVED: SPEC.md §3 FR-7 — English rendering of canonical Chinese clause; measurement boundary owned by test harness per canonical line. -->

<!-- DERIVED: SPEC.md §3 FR-7 — English rendering of canonical Chinese clause; measurement boundary owned by test harness per canonical line. -->

Revision v2 adds `tags`, `task_tags` (many-to-many) plus a unique index
on `tasks.name`; downgrade drops the new tables and index without
affecting v1 data (SPEC.md §3 FR-07).

#### AC-7.3
<!-- DERIVED: SPEC.md §3 FR-7 — English rendering of canonical Chinese clause; measurement boundary owned by test harness per canonical line. -->

<!-- DERIVED: SPEC.md §3 FR-7 — English rendering of canonical Chinese clause; measurement boundary owned by test harness per canonical line. -->

Revision v3 performs the data migration: splits `tasks.result_json`
into a separate `task_results` table, migrates existing data, then
drops the original column; downgrade reverses the move and drops
`task_results` (SPEC.md §3 FR-07).

#### AC-7.4
<!-- DERIVED: SPEC.md §3 FR-7 — English rendering of canonical Chinese clause; measurement boundary owned by test harness per canonical line. -->

<!-- DERIVED: SPEC.md §3 FR-7 — English rendering of canonical Chinese clause; measurement boundary owned by test harness per canonical line. -->

`alembic upgrade head` and `alembic downgrade base` both exit 0
(SPEC.md §3 FR-07, §8 #13).

#### AC-7.5
<!-- DERIVED: SPEC.md §3 FR-7 — English rendering of canonical Chinese clause; measurement boundary owned by test harness per canonical line. -->

<!-- DERIVED: SPEC.md §3 FR-7 — English rendering of canonical Chinese clause; measurement boundary owned by test harness per canonical line. -->

Round-trip test: `upgrade head` → write sample data → `downgrade -1` →
`upgrade head` leaves every column of the sample data byte-identical;
this is the focus of the v3 data-migration step (SPEC.md §3 FR-07, §8
#12).

#### AC-7.6
<!-- DERIVED: SPEC.md §3 FR-7 — English rendering of canonical Chinese clause; measurement boundary owned by test harness per canonical line. -->

<!-- DERIVED: SPEC.md §3 FR-7 — English rendering of canonical Chinese clause; measurement boundary owned by test harness per canonical line. -->

Migrations do not use `op.execute("DROP TABLE ...")` or other
destructive shortcuts to substitute for a real downgrade (SPEC.md §3
FR-07).

#### AC-7.7
<!-- DERIVED: SPEC.md §3 FR-7 — English rendering of canonical Chinese clause; measurement boundary owned by test harness per canonical line. -->

<!-- DERIVED: SPEC.md §3 FR-7 — English rendering of canonical Chinese clause; measurement boundary owned by test harness per canonical line. -->

Migration files are covered by tests (offline SQL generation plus
assertions) (SPEC.md §3 FR-07).

### FR-08: 非同步執行器

> Source: SPEC.md §3 FR-08. <!-- DERIVED: SPEC.md §3 — English rendering of canonical Chinese clause; measurement boundary owned by test harness per canonical line. -->

- 背景執行以 `asyncio.TaskGroup` 管理;服務關閉時必須 **graceful
  drain**(等待進行中的任務至 `TASKQ_DRAIN_TIMEOUT`,逾時則標記
  `interrupted`)
- 併發上限 `TASKQ_MAX_CONCURRENT`;超過時新任務排隊,不得無限制生成
  coroutine
- 任務 timeout 以 `asyncio.wait_for` 實作;逾時必須**確實終止子進程**
  (`process.kill()` 後 `await process.wait()`),不得留下孤兒進程
- 取消語意:`asyncio.CancelledError` 必須向上傳播,**不得被 `except
  Exception` 吞掉**(NFR-03)

#### AC-8.1
<!-- DERIVED: SPEC.md §3 FR-8 — English rendering of canonical Chinese clause; measurement boundary owned by test harness per canonical line. -->

<!-- DERIVED: SPEC.md §3 FR-8 — English rendering of canonical Chinese clause; measurement boundary owned by test harness per canonical line. -->

Background execution is managed by `asyncio.TaskGroup`; on shutdown the
service performs a graceful drain up to `TASKQ_DRAIN_TIMEOUT`, marking
exceeded tasks as `interrupted` (SPEC.md §3 FR-08, §5.1).

#### AC-8.2
<!-- DERIVED: SPEC.md §3 FR-8 — English rendering of canonical Chinese clause; measurement boundary owned by test harness per canonical line. -->

<!-- DERIVED: SPEC.md §3 FR-8 — English rendering of canonical Chinese clause; measurement boundary owned by test harness per canonical line. -->

Concurrency is capped at `TASKQ_MAX_CONCURRENT`; over-cap submissions
queue rather than spawning unbounded coroutines (SPEC.md §3 FR-08,
§5.1).

#### AC-8.3
<!-- DERIVED: SPEC.md §3 FR-8 — English rendering of canonical Chinese clause; measurement boundary owned by test harness per canonical line. -->

<!-- DERIVED: SPEC.md §3 FR-8 — English rendering of canonical Chinese clause; measurement boundary owned by test harness per canonical line. -->

Task timeout is enforced via `asyncio.wait_for`; on timeout the child
process is terminated by `process.kill()` followed by
`await process.wait()`; no orphan processes remain (SPEC.md §3 FR-08,
§8 #25).

#### AC-8.4
<!-- DERIVED: SPEC.md §3 FR-8 — English rendering of canonical Chinese clause; measurement boundary owned by test harness per canonical line. -->

<!-- DERIVED: SPEC.md §3 FR-8 — English rendering of canonical Chinese clause; measurement boundary owned by test harness per canonical line. -->

`asyncio.CancelledError` propagates upward; it is never caught by a
bare `except Exception:` block (SPEC.md §3 FR-08, NFR-03).

### FR-09: 健康檢查與可觀測性

> Source: SPEC.md §3 FR-09. <!-- DERIVED: SPEC.md §3 — English rendering of canonical Chinese clause; measurement boundary owned by test harness per canonical line. -->

| 端點 | 認證 | 行為 |
|------|------|------|
| `GET /healthz` | 無 | 進程存活 → 200 `{"status":"ok"}` |
| `GET /readyz` | 無 | DB 連線可用 **且** `alembic current` == head → 200;否則 **503** 並在 body 說明哪一項失敗 |
| `GET /v1/metrics` | `admin` | 任務計數(按狀態)、執行延遲分位數、rate-limit 拒絕數 |

- `/readyz` 的「migration 未到 head」判定是關鍵:部署了新程式碼但忘
  記跑 migration 時必須 **fail closed**

#### AC-9.1
<!-- DERIVED: SPEC.md §3 FR-9 — English rendering of canonical Chinese clause; measurement boundary owned by test harness per canonical line. -->

<!-- DERIVED: SPEC.md §3 FR-9 — English rendering of canonical Chinese clause; measurement boundary owned by test harness per canonical line. -->

`GET /healthz` (no auth) returns **HTTP 200** with `{"status":"ok"}`
when the process is alive (SPEC.md §3 FR-09).

#### AC-9.2
<!-- DERIVED: SPEC.md §3 FR-9 — English rendering of canonical Chinese clause; measurement boundary owned by test harness per canonical line. -->

<!-- DERIVED: SPEC.md §3 FR-9 — English rendering of canonical Chinese clause; measurement boundary owned by test harness per canonical line. -->

`GET /readyz` (no auth) returns **HTTP 200** iff the DB is reachable
**and** `alembic current` equals head; otherwise **HTTP 503** with a
body that names which check failed (SPEC.md §3 FR-09, §8 #10, §8 #11).

#### AC-9.3
<!-- DERIVED: SPEC.md §3 FR-9 — English rendering of canonical Chinese clause; measurement boundary owned by test harness per canonical line. -->

<!-- DERIVED: SPEC.md §3 FR-9 — English rendering of canonical Chinese clause; measurement boundary owned by test harness per canonical line. -->

`GET /v1/metrics` (admin scope) returns per-status task counts,
execution-latency percentiles, and rate-limit rejection counts
(SPEC.md §3 FR-09).

### FR-10: 錯誤契約(RFC 7807)

> Source: SPEC.md §3 FR-10. <!-- DERIVED: SPEC.md §3 — English rendering of canonical Chinese clause; measurement boundary owned by test harness per canonical line. -->

- 全部非 2xx 回應的 `Content-Type` 為 `application/problem+json`
- body 欄位:`type`(URI)、`title`、`status`、`detail`、`instance`、
  `correlation_id`
- **`detail` 不得洩漏內部細節**:不得含 SQL 陳述、堆疊追蹤、檔案路徑、
  資料庫結構描述
- `correlation_id` 同時出現在回應 header `X-Correlation-Id` 與伺服器
  日誌,可用於串接
- 錯誤碼對照:422 驗證 / 401 未認證 / 403 scope 不足 / 404 未知資源 /
  409 名稱衝突 / 429 超限 / 503 未就緒 / 500 其他

#### AC-10.1
<!-- DERIVED: SPEC.md §3 FR-10 — English rendering of canonical Chinese clause; measurement boundary owned by test harness per canonical line. -->

<!-- DERIVED: SPEC.md §3 FR-10 — English rendering of canonical Chinese clause; measurement boundary owned by test harness per canonical line. -->

Every non-2xx response has `Content-Type: application/problem+json`
(SPEC.md §3 FR-10, §7).

#### AC-10.2
<!-- DERIVED: SPEC.md §3 FR-10 — English rendering of canonical Chinese clause; measurement boundary owned by test harness per canonical line. -->

<!-- DERIVED: SPEC.md §3 FR-10 — English rendering of canonical Chinese clause; measurement boundary owned by test harness per canonical line. -->

Error body carries `type` (URI), `title`, `status`, `detail`,
`instance`, `correlation_id` (SPEC.md §3 FR-10).

#### AC-10.3
<!-- DERIVED: SPEC.md §3 FR-10 — English rendering of canonical Chinese clause; measurement boundary owned by test harness per canonical line. -->

<!-- DERIVED: SPEC.md §3 FR-10 — English rendering of canonical Chinese clause; measurement boundary owned by test harness per canonical line. -->

`detail` does not include SQL statements, stack traces, file paths, or
database schema descriptions (SPEC.md §3 FR-10, §8 #19).

#### AC-10.4
<!-- DERIVED: SPEC.md §3 FR-10 — English rendering of canonical Chinese clause; measurement boundary owned by test harness per canonical line. -->

<!-- DERIVED: SPEC.md §3 FR-10 — English rendering of canonical Chinese clause; measurement boundary owned by test harness per canonical line. -->

`correlation_id` appears both in the response header
`X-Correlation-Id` and in server logs (SPEC.md §3 FR-10).

#### AC-10.5
<!-- DERIVED: SPEC.md §3 FR-10 — English rendering of canonical Chinese clause; measurement boundary owned by test harness per canonical line. -->

<!-- DERIVED: SPEC.md §3 FR-10 — English rendering of canonical Chinese clause; measurement boundary owned by test harness per canonical line. -->

HTTP status mapping per SPEC.md §7:
422 → validation, 401 → unauthenticated, 403 → forbidden, 404 →
not-found, 409 → conflict, 429 → rate-limited (+ `Retry-After`), 503 →
not-ready, 500 → internal (SPEC.md §3 FR-10, §7).

---

## 4. Non-Functional Requirements

> Each NFR section is headed `### NFR-XX: <canonical title>`. Acceptance
> criteria carry stable `#### AC-Nx.y` identifiers. `dimension:` is one
> of the keys currently declared in
> `harness/harness/ssi/prompts/evaluate_dimension.md` §Step 1; the
> canonical `type:` for the FR Block follows the SAB NFR vocabulary
> pinned in `harness/core/quality_gate/sab_parser.ALL_NFR_TYPES`.

### NFR-01: 效能與查詢效率

> Source: SPEC.md §4 NFR-01. <!-- DERIVED: SPEC.md §4 — English rendering of canonical Chinese clause; measurement boundary owned by test harness per canonical line. -->

- **dimension**: `performance`
- `GET /v1/tasks/{id}` 在 10,000 筆資料下 **p95 < 30ms**(不含網路,以
  ASGI transport 量測)
- `GET /v1/tasks?limit=50` 在 10,000 筆資料下 **p95 < 80ms**
- **N+1 為失敗條件**:列表端點回應一次請求所發出的 SQL 陳述數必須是
  **常數 ≤ 4**(與回傳筆數無關);採 SQLAlchemy event listener 在
  **{1, 100, 1000, 10000}** 筆資料下計數,variance = 0;具體組成預期為
  1 × count + 1 × 主查詢 + ≤ 2 × 顯式 eager-loaded children 預載
- 量測方式:`pytest-benchmark`

#### AC-N1.1
<!-- DERIVED: SPEC.md §4 NFR-1 — English rendering of canonical Chinese clause; measurement boundary owned by test harness per canonical line. -->

`GET /v1/tasks/{id}` p95 < 30 ms at 10,000 rows, measured via
`pytest-benchmark` over ASGI transport (SPEC.md §4 NFR-01, §8 #15,
§11).

#### AC-N1.2
<!-- DERIVED: SPEC.md §4 NFR-1 — English rendering of canonical Chinese clause; measurement boundary owned by test harness per canonical line. -->

`GET /v1/tasks?limit=50` p95 < 80 ms at 10,000 rows, measured via
`pytest-benchmark` over ASGI transport (SPEC.md §4 NFR-01, §11).

#### AC-N1.3
<!-- DERIVED: SPEC.md §4 NFR-1 — English rendering of canonical Chinese clause; measurement boundary owned by test harness per canonical line. -->

The list endpoint issues a constant number of SQL statements (≤ 4)
regardless of the number of rows returned; SQL statement count is
measured via a SQLAlchemy event listener at dataset sizes
{1, 100, 1000, 10000} and the variance is 0 (SPEC.md §4 NFR-01, §8 #14,
§11).

#### AC-N1.4
<!-- DERIVED: SPEC.md §4 NFR-1 — English rendering of canonical Chinese clause; measurement boundary owned by test harness per canonical line. -->

The expected statement-count composition is `1 × count + 1 × main
query + ≤ 2 × explicit eager-loaded children` (SPEC.md §4 NFR-01).

### NFR-02: HTTP 與資料層安全

> Source: SPEC.md §4 NFR-02. <!-- DERIVED: SPEC.md §4 — English rendering of canonical Chinese clause; measurement boundary owned by test harness per canonical line. -->

- **dimension**: `security`
- 全 codebase 禁用 `shell=True`、`eval(`、`exec(`(grep 0 命中)
- **禁止字串拼接 SQL**:不得出現 f-string / `%` / `+` 組成的 SQL;一律
  ORM 或參數化(以 grep + code review 雙重驗證)
- API key **雜湊儲存**,比對用 `hmac.compare_digest`(FR-03)
- 403 回應不得洩漏資源存在性(FR-04)
- 錯誤 body 不得含堆疊/SQL/路徑(FR-10)
- CORS 預設**拒絕所有來源**;允許清單由 `TASKQ_CORS_ORIGINS` 明示
- `bandit -r ${SOURCE_ROOT}/src/`:**0 HIGH、0 MEDIUM**

#### AC-N2.1
<!-- DERIVED: SPEC.md §4 NFR-2 — English rendering of canonical Chinese clause; measurement boundary owned by test harness per canonical line. -->

`grep -rn "shell=True\|eval(\|exec(" src/` returns **0 hits**
(SPEC.md §4 NFR-02, §8 #16).

#### AC-N2.2
<!-- DERIVED: SPEC.md §4 NFR-2 — English rendering of canonical Chinese clause; measurement boundary owned by test harness per canonical line. -->

No string-concatenated SQL exists in `src/`; a grep gate for f-string /
`%` / `+` SQL composition returns **0 hits** (SPEC.md §4 NFR-02, §8 #17).

#### AC-N2.3
<!-- DERIVED: SPEC.md §4 NFR-2 — English rendering of canonical Chinese clause; measurement boundary owned by test harness per canonical line. -->

API keys are stored hashed and compared with `hmac.compare_digest`
(SPEC.md §4 NFR-02, FR-03).

#### AC-N2.4
<!-- DERIVED: SPEC.md §4 NFR-2 — English rendering of canonical Chinese clause; measurement boundary owned by test harness per canonical line. -->

403 responses do not leak resource existence (SPEC.md §4 NFR-02,
FR-04).

#### AC-N2.5
<!-- DERIVED: SPEC.md §4 NFR-2 — English rendering of canonical Chinese clause; measurement boundary owned by test harness per canonical line. -->

Error bodies contain no stack traces, SQL statements, or file paths
(SPEC.md §4 NFR-02, FR-10, §8 #19).

#### AC-N2.6
<!-- DERIVED: SPEC.md §4 NFR-2 — English rendering of canonical Chinese clause; measurement boundary owned by test harness per canonical line. -->

CORS denies all origins by default; the allowlist is configured via
`TASKQ_CORS_ORIGINS` (SPEC.md §4 NFR-02, §5.1).

#### AC-N2.7
<!-- DERIVED: SPEC.md §4 NFR-2 — English rendering of canonical Chinese clause; measurement boundary owned by test harness per canonical line. -->

`bandit -r src/` reports **0 HIGH** and **0 MEDIUM** issues
(SPEC.md §4 NFR-02, §8 #23).

### NFR-03: 錯誤處理、交易與非同步正確性

> Source: SPEC.md §4 NFR-03. <!-- DERIVED: SPEC.md §4 — English rendering of canonical Chinese clause; measurement boundary owned by test harness per canonical line. -->

- **dimension**: `error_handling`
- 每個請求的交易邊界明確:成功 commit、例外 rollback,以 context
  manager 保證(FR-06)
- **不得**出現裸 `except:`、`except Exception: pass`
- **`asyncio.CancelledError` 不得被吞掉** —— 必須重新拋出(async
  專屬的吞噬陷阱)
- 資料庫連線失敗 → `/readyz` 503 + 明確 detail;不得靜默重試至無限
- 任務 timeout 必須確實終止子進程,不留孤兒(FR-08)
- migration 失敗 → 交易 rollback,資料庫維持在前一個 revision(FR-07)

#### AC-N3.1
<!-- DERIVED: SPEC.md §4 NFR-3 — English rendering of canonical Chinese clause; measurement boundary owned by test harness per canonical line. -->

Each request has an explicit transaction boundary; context manager
commits on success and rolls back on exception (SPEC.md §4 NFR-03,
FR-06).

#### AC-N3.2
<!-- DERIVED: SPEC.md §4 NFR-3 — English rendering of canonical Chinese clause; measurement boundary owned by test harness per canonical line. -->

No bare `except:` or `except Exception: pass` appears in `src/`
(SPEC.md §4 NFR-03).

#### AC-N3.3
<!-- DERIVED: SPEC.md §4 NFR-3 — English rendering of canonical Chinese clause; measurement boundary owned by test harness per canonical line. -->

`asyncio.CancelledError` is never swallowed; it is re-raised in any
catch site that touches it (SPEC.md §4 NFR-03, FR-08).

#### AC-N3.4
<!-- DERIVED: SPEC.md §4 NFR-3 — English rendering of canonical Chinese clause; measurement boundary owned by test harness per canonical line. -->

Database connection failure causes `/readyz` to return 503 with a
clear `detail`; the application does not silently retry forever
(SPEC.md §4 NFR-03).

#### AC-N3.5
<!-- DERIVED: SPEC.md §4 NFR-3 — English rendering of canonical Chinese clause; measurement boundary owned by test harness per canonical line. -->

Task timeout actually terminates the subprocess; no orphan processes
remain (SPEC.md §4 NFR-03, FR-08, §8 #25).

#### AC-N3.6
<!-- DERIVED: SPEC.md §4 NFR-3 — English rendering of canonical Chinese clause; measurement boundary owned by test harness per canonical line. -->

Migration failure rolls back the transaction; the database stays at
the previous revision (SPEC.md §4 NFR-03, FR-07).

### NFR-04: 敏感資料遮蔽

> Source: SPEC.md §4 NFR-04. <!-- DERIVED: SPEC.md §4 — English rendering of canonical Chinese clause; measurement boundary owned by test harness per canonical line. -->

- **dimension**: `security`
- `stdout_tail` / `stderr_tail` / 日誌 / 錯誤 body 落盤或送出前,匹配
  `(sk-[A-Za-z0-9_-]{8,}|token=\S+|Bearer\s+\S+|postgres(ql)?://[^\s]+)`
  的行整行以 `[REDACTED]` 取代
- **資料庫連線字串**(含密碼)不得出現在任何日誌、錯誤訊息或
  `/v1/metrics` 回應中
- API key 明文只在 `key create` 當下輸出一次,不得寫入任何持久化位置

#### AC-N4.1
<!-- DERIVED: SPEC.md §4 NFR-4 — English rendering of canonical Chinese clause; measurement boundary owned by test harness per canonical line. -->

Lines matching `(sk-[A-Za-z0-9_-]{8,}|token=\S+|Bearer\s+\S+|postgres(ql)?://[^\s]+)`
in `stdout_tail` / `stderr_tail` / logs / error bodies are replaced in
full with `[REDACTED]` before write or emit (SPEC.md §4 NFR-04).

#### AC-N4.2
<!-- DERIVED: SPEC.md §4 NFR-4 — English rendering of canonical Chinese clause; measurement boundary owned by test harness per canonical line. -->

The database connection string (including the password component)
does not appear in any log, error message, or `/v1/metrics` response
(SPEC.md §4 NFR-04, §8 #20).

#### AC-N4.3
<!-- DERIVED: SPEC.md §4 NFR-4 — English rendering of canonical Chinese clause; measurement boundary owned by test harness per canonical line. -->

API key plaintext is printed exactly once at `key create` time; it is
never written to any persistent location (SPEC.md §4 NFR-04, FR-03).

### NFR-05: 文件覆蓋

> Source: SPEC.md §4 NFR-05. <!-- DERIVED: SPEC.md §4 — English rendering of canonical Chinese clause; measurement boundary owned by test harness per canonical line. -->

- **dimension**: `documentation`
- 全部公開函式/類別有 docstring 且含 `[FR-XX]` 或 `[NFR-XX]` 引用,
  覆蓋率 **100%**
- 每個 API 端點在 OpenAPI schema 中有 `summary` 與 `description`
  (FastAPI 自動產生的 `/openapi.json` 以測試斷言)

#### AC-N5.1
<!-- DERIVED: SPEC.md §4 NFR-5 — English rendering of canonical Chinese clause; measurement boundary owned by test harness per canonical line. -->

Every public function/class has a docstring referencing `[FR-XX]` or
`[NFR-XX]`; public-API docstring coverage is **100%** (SPEC.md §4
NFR-05).

#### AC-N5.2
<!-- DERIVED: SPEC.md §4 NFR-5 — English rendering of canonical Chinese clause; measurement boundary owned by test harness per canonical line. -->

Every API endpoint has `summary` and `description` in the OpenAPI
schema; a test asserts these fields in `/openapi.json` (SPEC.md §4
NFR-05).

### NFR-06: 架構分層契約

> Source: SPEC.md §4 NFR-06. <!-- DERIVED: SPEC.md §4 — English rendering of canonical Chinese clause; measurement boundary owned by test harness per canonical line. -->

- **dimension**: `architecture_constraints`
- 專案根目錄**必須存在 `.importlinter`**,宣告 layers contract:
  `api > service > repository > models`。上層可 import 下層,**下層不得
  import 上層**;`config` 與 `errors` 為 independence 模組
- **額外禁令(forbidden contract)**:`repository` 以外的任何層**不得
  import `sqlalchemy`** —— ORM 洩漏到業務層是本輪要防的具體反模式
- `lint-imports` 必須 **exit 0**
- 禁止以刪除 `.importlinter`、萬用字元 `ignore_imports`、或降級
  contract 的方式取得通過

#### AC-N6.1
<!-- DERIVED: SPEC.md §4 NFR-6 — English rendering of canonical Chinese clause; measurement boundary owned by test harness per canonical line. -->

Project root contains `.importlinter` declaring the layers contract
`api > service > repository > models`; upper layers may import lower
layers, lower layers must not import upper layers; `config` and
`errors` are declared as independence modules (SPEC.md §4 NFR-06).

#### AC-N6.2
<!-- DERIVED: SPEC.md §4 NFR-6 — English rendering of canonical Chinese clause; measurement boundary owned by test harness per canonical line. -->

A forbidden contract in `.importlinter` bans any non-`repository` layer
from importing `sqlalchemy` (SPEC.md §4 NFR-06).

#### AC-N6.3
<!-- DERIVED: SPEC.md §4 NFR-6 — English rendering of canonical Chinese clause; measurement boundary owned by test harness per canonical line. -->

`lint-imports` exits 0 (SPEC.md §4 NFR-06, §8 #21).

#### AC-N6.4
<!-- DERIVED: SPEC.md §4 NFR-6 — English rendering of canonical Chinese clause; measurement boundary owned by test harness per canonical line. -->

Passing the gate does not require removing `.importlinter`, using
wildcards in `ignore_imports`, or downgrading the contract (SPEC.md §4
NFR-06).

### NFR-07: 依賴與授權合規

> Source: SPEC.md §4 NFR-07. <!-- DERIVED: SPEC.md §4 — English rendering of canonical Chinese clause; measurement boundary owned by test harness per canonical line. -->

- **dimension**: `license_compliance`
- 全部 runtime 依賴在 `requirements.txt` 以 `==` 釘版;**transitive
  依賴以 lock 檔(`requirements.lock`)完整鎖定**
- 允許的 license:MIT / BSD-2-Clause / BSD-3-Clause / Apache-2.0 /
  PSF;出現其他 → 該依賴不得使用
- **掃描範圍必須包含完整依賴樹**(直接 + transitive),證據命令:
  `pip-licenses --format=json --with-system`
- 產出 SBOM 檔案(由框架決定具體路徑),含每個依賴的 `name` /
  `version` / `license` / `direct|transitive`

#### AC-N7.1
<!-- DERIVED: SPEC.md §4 NFR-7 — English rendering of canonical Chinese clause; measurement boundary owned by test harness per canonical line. -->

All runtime dependencies in `requirements.txt` are pinned with `==`;
transitive dependencies are fully pinned via `requirements.lock`
(SPEC.md §4 NFR-07).

#### AC-N7.2
<!-- DERIVED: SPEC.md §4 NFR-7 — English rendering of canonical Chinese clause; measurement boundary owned by test harness per canonical line. -->

Allowed licenses are MIT / BSD-2-Clause / BSD-3-Clause / Apache-2.0 /
PSF; any dependency with another license must not be used (SPEC.md §4
NFR-07, §8 #22).

#### AC-N7.3
<!-- DERIVED: SPEC.md §4 NFR-7 — English rendering of canonical Chinese clause; measurement boundary owned by test harness per canonical line. -->

License scan covers the full dependency tree (direct + transitive);
the evidence command is `pip-licenses --format=json --with-system`
(SPEC.md §4 NFR-07, §8 #22).

#### AC-N7.4
<!-- DERIVED: SPEC.md §4 NFR-7 — English rendering of canonical Chinese clause; measurement boundary owned by test harness per canonical line. -->

An SBOM artefact is produced (concrete path decided by framework)
containing per-dependency `name` / `version` / `license` /
`direct|transitive` (SPEC.md §4 NFR-07).

### NFR-08: 變異測試

> Source: SPEC.md §4 NFR-08. <!-- DERIVED: SPEC.md §4 — English rendering of canonical Chinese clause; measurement boundary owned by test harness per canonical line. -->

- **dimension**: `mutation_testing`
- `.methodology/harness_config.json` 設
  `features.mutation_testing: true`
- **mutation score ≥ 70**
- 範圍限定於 **業務邏輯層(services)** 與 **資料存取層
  (repositories)** 兩個架構分層(對應 NFR-06 layers contract),並在
  `harness_config.json` 註記限定理由(執行時間預算);具體目錄與檔名
  由框架決定

#### AC-N8.1
<!-- DERIVED: SPEC.md §4 NFR-8 — English rendering of canonical Chinese clause; measurement boundary owned by test harness per canonical line. -->

`.methodology/harness_config.json` sets
`features.mutation_testing: true` (SPEC.md §4 NFR-08).

#### AC-N8.2
<!-- DERIVED: SPEC.md §4 NFR-8 — English rendering of canonical Chinese clause; measurement boundary owned by test harness per canonical line. -->

Mutation score is **≥ 70** over the services and repositories layers
(NFR-06 roles) (SPEC.md §4 NFR-08, §8 #24).

#### AC-N8.3
<!-- DERIVED: SPEC.md §4 NFR-8 — English rendering of canonical Chinese clause; measurement boundary owned by test harness per canonical line. -->

Scope is restricted to the two NFR-06 layers (services +
repositories); the runtime-budget rationale is recorded in
`harness_config.json` (SPEC.md §4 NFR-08).

### NFR-09: 驗證真實性(零 skip 鐵律)

> Source: SPEC.md §4 NFR-09. <!-- DERIVED: SPEC.md §4 — English rendering of canonical Chinese clause; measurement boundary owned by test harness per canonical line. -->

- **dimension**: `test_assertion_quality`
- **任何 FR / NFR 的驗證測試不得是 `pytest.skip` / `skipif` / `xfail` /
  無斷言的 stub**
- `pytest ${SOURCE_ROOT}/tests -q` 的 **skipped 計數必須為 0**
- 每個測試函式至少一個 `assert`(`zero_assert == 0`)
- **反造假條款**:不得以 `--ignore` / `-k` / `--deselect` /
  `collect_ignore` / 從 `testpaths` 移除目錄的方式排除測試
- **本輪特別條款**:`FR-07` 的三步 migration 必須以**真實資料庫**測
  試(SQLite 檔案,非 in-memory mock),往返可逆性以實際資料比對驗證
  。**不得**以「migration 邏輯太難測」為由降級為 skip —— 這正是前
  兩輪失敗的形態
- `TRACEABILITY_MATRIX.md` 的 `VERIFIED` 只能在測試實際執行並通過時
  給出

#### AC-N9.1
<!-- DERIVED: SPEC.md §4 NFR-9 — English rendering of canonical Chinese clause; measurement boundary owned by test harness per canonical line. -->

No FR/NFR verification test is marked `pytest.skip` / `skipif` /
`xfail`, and no test is an assertion-free stub (SPEC.md §4 NFR-09, §8
#1).

#### AC-N9.2
<!-- DERIVED: SPEC.md §4 NFR-9 — English rendering of canonical Chinese clause; measurement boundary owned by test harness per canonical line. -->

`pytest tests -q` reports **skipped = 0** (SPEC.md §4 NFR-09, §8 #1,
§11).

#### AC-N9.3
<!-- DERIVED: SPEC.md §4 NFR-9 — English rendering of canonical Chinese clause; measurement boundary owned by test harness per canonical line. -->

Every test function contains at least one `assert` (`zero_assert == 0`)
(SPEC.md §4 NFR-09, §11).

#### AC-N9.4
<!-- DERIVED: SPEC.md §4 NFR-9 — English rendering of canonical Chinese clause; measurement boundary owned by test harness per canonical line. -->

No test is excluded via `--ignore` / `-k` / `--deselect` /
`collect_ignore` / removal from `testpaths` (SPEC.md §4 NFR-09).

#### AC-N9.5
<!-- DERIVED: SPEC.md §4 NFR-9 — English rendering of canonical Chinese clause; measurement boundary owned by test harness per canonical line. -->

The three-step FR-07 migration is tested against a real database
(SQLite file, not an in-memory mock); round-trip reversibility is
verified by actual data comparison (SPEC.md §4 NFR-09, FR-07).

#### AC-N9.6
<!-- DERIVED: SPEC.md §4 NFR-9 — English rendering of canonical Chinese clause; measurement boundary owned by test harness per canonical line. -->

Migration logic is **not** downgraded to `skip` on the grounds of
being "too hard to test" (SPEC.md §4 NFR-09).

#### AC-N9.7
<!-- DERIVED: SPEC.md §4 NFR-9 — English rendering of canonical Chinese clause; measurement boundary owned by test harness per canonical line. -->

`TRACEABILITY_MATRIX.md` marks a requirement `VERIFIED` only after
the corresponding test actually runs and passes (SPEC.md §4 NFR-09).

### NFR-10: 整合覆蓋

> Source: SPEC.md §4 NFR-10. <!-- DERIVED: SPEC.md §4 — English rendering of canonical Chinese clause; measurement boundary owned by test harness per canonical line. -->

- **dimension**: `integration_coverage`
- `${SOURCE_ROOT}/tests/integration/` 行覆蓋 **≥ 80%**
- 整合測試以 `httpx.AsyncClient(transport=ASGITransport(app))` 驅動,
  **不得直接呼叫 handler 函式**
- 至少涵蓋:CRUD 全鏈、401/403/404/409/422/429/503 每個錯誤碼各一例
  (500 由 §8 #19 觸發,**不**強制每輪都出現,因為屬於「unexpected」
  類別)、migration 往返、rate limit 觸發與恢復、graceful drain

#### AC-N10.1
<!-- DERIVED: SPEC.md §4 NFR-10 — English rendering of canonical Chinese clause; measurement boundary owned by test harness per canonical line. -->

`tests/integration/` line coverage is **≥ 80%** over `src/`
(SPEC.md §4 NFR-10, §8 #3, §11).

#### AC-N10.2
<!-- DERIVED: SPEC.md §4 NFR-10 — English rendering of canonical Chinese clause; measurement boundary owned by test harness per canonical line. -->

Integration tests are driven through
`httpx.AsyncClient(transport=ASGITransport(app))`; they do not call
handler functions directly (SPEC.md §4 NFR-10).

#### AC-N10.3
<!-- DERIVED: SPEC.md §4 NFR-10 — English rendering of canonical Chinese clause; measurement boundary owned by test harness per canonical line. -->

Coverage scope includes the full CRUD chain, each error code
(401/403/404/409/422/429/503) at least once, migration round-trip,
rate-limit trigger and recovery, and graceful drain (SPEC.md §4
NFR-10).

### NFR-11: 可讀性

> Source: SPEC.md §4 NFR-11. <!-- DERIVED: SPEC.md §4 — English rendering of canonical Chinese clause; measurement boundary owned by test harness per canonical line. -->

- **dimension**: `readability`
- 專案 MI(LLOC 加權)**≥ 80**;單一函式 CC **≤ 10**
- 單一檔案 ≤ 400 行;單一目錄 ≤ 15 檔
- 每個 API handler ≤ 40 行(業務邏輯必須下沉到業務邏輯層;對應 NFR-06
  layers contract 的 `service` 角色),具體目錄由框架決定

#### AC-N11.1
<!-- DERIVED: SPEC.md §4 NFR-11 — English rendering of canonical Chinese clause; measurement boundary owned by test harness per canonical line. -->

Project MI (LLOC-weighted) ≥ 80; single function CC ≤ 10 (SPEC.md §4
NFR-11, §11).

#### AC-N11.2
<!-- DERIVED: SPEC.md §4 NFR-11 — English rendering of canonical Chinese clause; measurement boundary owned by test harness per canonical line. -->

A single source file is ≤ 400 lines; a single source directory
contains ≤ 15 files (SPEC.md §4 NFR-11).

#### AC-N11.3
<!-- DERIVED: SPEC.md §4 NFR-11 — English rendering of canonical Chinese clause; measurement boundary owned by test harness per canonical line. -->

Each API handler is ≤ 40 lines; business logic descends to the
service layer (SPEC.md §4 NFR-11).

### NFR-12: 系統驗證目標

> Source: SPEC.md §4 NFR-12. <!-- DERIVED: SPEC.md §4 — English rendering of canonical Chinese clause; measurement boundary owned by test harness per canonical line. -->

- **dimension**: `execute_verification_target`
- `Makefile` 的 `verify-system` target 必須串接:
  1. `alembic upgrade head`
  2. 全套測試
  3. 服務啟動 + `/healthz`、`/readyz` 冒煙
  4. `alembic downgrade base` 後再 `upgrade head`(往返驗證)
- `make verify-system` 必須 **exit 0** 並在 stdout 印出
  `verify-system: PASS`

#### AC-N12.1
<!-- DERIVED: SPEC.md §4 NFR-12 — English rendering of canonical Chinese clause; measurement boundary owned by test harness per canonical line. -->

The `Makefile` defines a `verify-system` target that chains:
(1) `alembic upgrade head`, (2) full test suite, (3) service start +
`/healthz` / `/readyz` smoke, (4) `alembic downgrade base` then
`upgrade head` (round-trip verification) (SPEC.md §4 NFR-12).

#### AC-N12.2
<!-- DERIVED: SPEC.md §4 NFR-12 — English rendering of canonical Chinese clause; measurement boundary owned by test harness per canonical line. -->

`make verify-system` exits 0 and prints `verify-system: PASS` to
stdout (SPEC.md §4 NFR-12, §8 #27).

---

## 5. Acceptance Criteria Summary

> Transcribed from SPEC.md §8 (27 acceptance items — each a single
> machine-decidable command with expected output) and §11 (monitoring
> thresholds). Used by Gate 1+ scoring.

### 5.1 SPEC.md §8 Acceptance items

| # | Command | Expected |
|---|---------|----------|
| 1 | `pytest ${SOURCE_ROOT}/tests -q` | 全綠,**skipped 計數為 0**(NFR-09) |
| 2 | `pytest ${SOURCE_ROOT}/tests --cov=${SOURCE_ROOT}/src --cov-report=term` | TOTAL **100%** |
| 3 | `pytest ${SOURCE_ROOT}/tests/integration --cov=${SOURCE_ROOT}/src --cov-report=term` | TOTAL **≥ 80%**(NFR-10) |
| 4 | `POST /v1/tasks`(有效 write key) | 201 + task id |
| 5 | `POST /v1/tasks`(無 `X-API-Key`) | **401** + problem+json |
| 6 | `DELETE /v1/tasks/{id}`(write key,非 admin) | **403**,body 不透露該 id 是否存在 |
| 7 | `GET /v1/tasks/{unknown}` | **404** + problem+json |
| 8 | `POST /v1/tasks` 重複 name | **409** |
| 9 | 連續請求超過 `TASKQ_RATE_BURST` | **429** + `Retry-After` header |
| 10 | 停掉 DB 後 `GET /readyz` | **503**,detail 指明 DB 不可用 |
| 11 | `alembic downgrade -1` 後 `GET /readyz` | **503**,detail 指明 migration 未到 head |
| 12 | `alembic upgrade head` → 寫樣本 → `downgrade -1` → `upgrade head` | 樣本資料逐欄相同(**v3 資料搬遷可逆** — FR-07) |
| 13 | `alembic downgrade base` | exit 0,無殘留表 |
| 14 | `GET /v1/tasks?limit=50`(10,000 筆)的 SQL 陳述計數 | **≤ 4 個陳述**,在 {1, 100, 1000, 10000} 筆下 variance = 0(N+1 防護,NFR-01) |
| 15 | `GET /v1/tasks/{id}` p95(10,000 筆) | **< 30ms**(NFR-01) |
| 16 | `grep -rn "shell=True\|eval(\|exec(" ${SOURCE_ROOT}/src/` | **0 命中** |
| 17 | 掃描 SQL 字串拼接(f-string / `%` / `+` 組 SQL) | **0 命中**(NFR-02) |
| 18 | 查 `api_keys` 表 | 無明文金鑰;`key_hash` 為 64 hex(NFR-02) |
| 19 | 觸發 500 後檢查回應 body | 不含堆疊 / SQL / 檔案路徑(FR-10 / NFR-02) |
| 20 | 日誌與 `/v1/metrics` 全文 | 不含 `TASKQ_DB_URL` 的密碼片段(NFR-04) |
| 21 | `lint-imports` | **exit 0**,且 `service`/`api` 層 import `sqlalchemy` 會被擋(NFR-06) |
| 22 | `pip-licenses --format=json --with-system` | 每個依� license ∈ allowlist(NFR-07) |
| 23 | `bandit -r ${SOURCE_ROOT}/src/` | 0 HIGH,0 MEDIUM |
| 24 | `mutmut run` 後 `mutmut results` | mutation score **≥ 70**(NFR-08) |
| 25 | 服務關閉時有進行中的任務 | graceful drain;逾時者標記 `interrupted`,無孤兒進程(FR-08) |
| 26 | `grep -c "^TASKQ_" .env.example` | **12**(§5.1 全部宣告) |
| 27 | `make verify-system` | exit 0 且 stdout 含 `verify-system: PASS`(NFR-12) |

### 5.2 SPEC.md §11 Monitoring thresholds

| 指標 | 閾值 | 量測方式 |
|------|------|---------|
| `GET /v1/tasks/{id}` p95(10k 筆) | < 30ms | pytest-benchmark(NFR-01) |
| `GET /v1/tasks?limit=50` p95(10k 筆) | < 80ms | pytest-benchmark(NFR-01) |
| 列表端點 SQL 陳述數 | **≤ 4 個陳述,在 {1, 100, 1000, 10000} 筆下 variance = 0** | SQLAlchemy event listener(NFR-01) |
| 測試 skip 數 | **0** | `pytest -q` 輸出(NFR-09) |
| 零斷言測試函式數 | **0** | ast-assertions(NFR-09) |
| 行覆蓋率 | 100% | pytest-cov |
| 整合覆蓋率 | ≥ 80% | pytest-cov-integration(NFR-10) |
| migration 往返資料一致 | 100%(逐欄) | 真實 SQLite 檔案測試(FR-07) |
| mutation score | ≥ 70 | mutmut(NFR-08) |
| `lint-imports` 違規 | 0 | import-linter(NFR-06) |
| `service`/`api` 層的 `sqlalchemy` import | 0 | import-linter forbidden contract(NFR-06) |
| 非 allowlist license(含 transitive) | 0 | pip-licenses(NFR-07) |
| SQL 字串拼接命中 | 0 | grep CI gate(NFR-02) |
| bandit HIGH / MEDIUM | 0 / 0 | bandit(NFR-02) |
| 錯誤 body 洩漏內部細節 | 0 | integration test(FR-10) |
| DB 連線字串出現於日誌 | 0 | unit test(NFR-04) |
| 孤兒子進程 | 0 | integration test(FR-08) |
| 專案 MI | ≥ 80 | readability-v2(NFR-11) |
| secrets 掃描命中 | 0 | gitleaks(對應 §10 `secrets_scanning` dimension) | NFR-02 |
| `make verify-system` | exit 0 | Makefile(NFR-12) |

### 5.3 Environment variables (SPEC.md §5.1)

| 變數 | 預設 | 說明 |
|------|------|------|
| `TASKQ_DB_URL` | `sqlite:///./taskq.db` | 資料庫連線字串(**不得**出現在日誌 — NFR-04) |
| `TASKQ_DB_POOL_SIZE` | `5` | 連線池大小(FR-06) |
| `TASKQ_TASK_TIMEOUT` | `10.0` | 單任務 subprocess timeout(秒) |
| `TASKQ_MAX_CONCURRENT` | `8` | 背景執行併發上限(FR-08) |
| `TASKQ_DRAIN_TIMEOUT` | `30.0` | 關閉時 graceful drain 上限(秒) |
| `TASKQ_RATE_BURST` | `20` | 令牌桶容量(FR-05) |
| `TASKQ_RATE_PER_SEC` | `5.0` | 令牌補充速率(FR-05) |
| `TASKQ_CORS_ORIGINS` | (空字串) | CORS 允許來源,逗號分隔;空 = 全拒(NFR-02) |
| `TASKQ_LOG_LEVEL` | `INFO` | `DEBUG` / `INFO` / `WARNING` / `ERROR` |
| `TASKQ_LOG_FORMAT` | `json` | `json` / `text` |
| `TASKQ_HOST` | `127.0.0.1` | 監聽位址(預設**不**對外) |
| `TASKQ_PORT` | `8000` | 監聽埠 |

### 5.4 Database schema (SPEC.md §5.2)

| 表 | revision | 主要欄位 |
|---|---|---|
| `tasks` | v1 | `id`(uuid)、`command`、`name`、`status`、`created_at` |
| `api_keys` | v1 | `id`、`key_hash`(sha256)、`scope`、`created_at`、`revoked_at` |
| `tags` | v2 | `id`、`label` |
| `task_tags` | v2 | `task_id`、`tag_id`(複合主鍵) |
| `task_results` | **v3** | `id`、`task_id`(FK)、`exit_code`、`stdout_tail`、`stderr_tail`、`duration_ms`、`finished_at` |
| `rate_buckets` | v1 | `key_id`(FK)、`tokens`、`updated_at` |

> `tasks.result_json` 在 v1 建立、v3 移除(資料搬遷至 `task_results`)。
> 這一步是 FR-07 往返可逆性驗收的重點。

### 5.5 Project-side required configuration files (SPEC.md §5.3)

| 檔案 | 用途 | 對應 |
|------|------|------|
| `.importlinter` | 分層契約 + `sqlalchemy` 禁令 | NFR-06(具體位置由框架決定) |
| `requirements.txt` + `requirements.lock` | 釘版 + transitive 鎖定 | NFR-07 |
| `requirements-dev.txt` | `import-linter` / `pip-licenses` / `mutmut` / `pytest-benchmark` / `httpx` | NFR-06/07/08/10 |
| `alembic.ini` + framework 決定的 migration 目錄 | 三個 revision(FR-07) | FR-07 |
| `.env.example` | 全部 12 個 `TASKQ_*` 逐一宣告並附註解 | §5.1 |
| `.methodology/harness_config.json` | `features.mutation_testing: true`;不得調降 `crg_cohesion_healthy` | NFR-08(具體位置由框架決定) |
| `Makefile` | `verify-system`(含 migration 往返) | NFR-12 |

---

## 6. Out-of-Scope

- **Folder / module layout**: SPEC.md §5.3 / §10 explicitly defer concrete
  directory paths and module file names to the framework ("角色不變,
  路徑變"). NFR-06 retains the role contract; the path is framework-owned.
- **Multi-tenant isolation beyond per-token scope**: only per-token
  scope and per-token rate limit are in scope; cross-tenant row-level
  isolation is not in scope for this round.
- **Webhook delivery / push notifications on task completion**: not
  specified in SPEC.md §3; deferred.
- **Distributed task scheduler (Celery / RQ / etc.)**: SPEC.md §3 FR-08
  specifies an in-process asyncio runner only; no external scheduler.
- **Schema-versioned HTTP API**: `/v1/` is the only API surface; no
  multi-version routing.
- **Audit log / immutable event stream**: not specified; deferred.
- **Multi-region / replication**: not specified.

---

## 7. Open Issues (deferred items)

> SPEC.md §0 placeholder rule: any `TBD` / `TODO` / `<placeholder>` /
  unspecified marker must surface here as `NFR-99` or `FR-XX-deferred`
  rather than being silently dropped. SPEC.md v1.0.0 has no explicit
  placeholders; the items below are framework-owned decisions that the
  spec intentionally leaves to the implementation agent, captured here
  so downstream phases do not re-litigate them.

### NFR-99: Framework-owned implementation paths
<!-- DERIVED: SPEC.md §0 — section captured by canonical placeholder rule; content is framework-owned per canonical. -->

- **Issue**: SPEC.md §3 / §5.3 / §10 mark four architectural **roles**
  as required but defer the concrete module path / file name to the
  framework:
  - async sub-process runner (FR-08)
  - single auth/authz decision layer (FR-04)
  - DB transaction-boundary context manager (FR-06)
  - v3 data-migration revision file (FR-07)
- **Current SPEC phrasing is unambiguous on roles** but does not bind
  module paths; the canonical interpretation is "role names are fixed;
  paths are framework-owned". This is **not** an ambiguity that needs
  stakeholder resolution — it is the spec's own design choice.
- **Status**: deferred to implementation (P3) per SPEC.md §10 「角色不
  變,路徑變」.

### FR-99-deferred: SPEC §6 (folder structure) was explicitly removed
<!-- DERIVED: SPEC.md §0 — section captured by canonical placeholder rule; content is framework-owned per canonical. -->

- **Issue**: SPEC.md §5.3 footnote states "§6 (資料夾結構) 已移除:由
  框架決定". No folder-structure section exists in v1.0.0.
- **Status**: deferred to framework (P3).

---

## 8. Risks

> Transcribed from SPEC.md §9.

| ID | 風險 | 影響 | 可能性 | 緩解 |
|----|------|------|--------|------|
| R1 | **v3 資料搬遷遺失資料** | **高** | 中 | 往返可逆性測試以真實 DB 逐欄比對(FR-07 / §8 #12) |
| R2 | SQL injection | 高 | 低 | 禁字串拼接 + ORM/參數化 + grep gate(NFR-02) |
| R3 | API key 洩漏 | 高 | 中 | 雜湊儲存 + 常數時間比對 + 明文只印一次(FR-03) |
| R4 | 403 洩漏資源存在性 | 中 | 中 | 授權判定在資源查詢之前(FR-04 / §8 #6) |
| R5 | N+1 查詢在大表上崩潰 | 高 | 高 | 顯式預載 + SQL 計數斷言(NFR-01 / §8 #14) |
| R6 | 錯誤 body 洩漏內部結構 | 中 | 高 | RFC 7807 固定欄位 + detail 白名單(FR-10) |
| R7 | **`CancelledError` 被吞 → 關閉時卡死** | 中 | 中 | 明文禁令 + 測試斷言(NFR-03) |
| R8 | 任務 timeout 留下孤兒進程 | 中 | 中 | `kill()` + `await wait()`(FR-08 / §8 #25) |
| R9 | 部署後忘記跑 migration | 高 | 中 | `/readyz` fail closed(FR-09 / §8 #11) |
| R10 | 連線池耗盡 | 中 | 中 | `pool_pre_ping` + 併發上限(FR-06/08) |
| R11 | transitive 依賴引入不相容 license | 中 | 中 | lock 檔 + 全樹掃描(NFR-07) |
| R12 | rate bucket 競態導致超放行 | 低 | 中 | 單一交易 + row-level lock(FR-05) |

---

## 9. Glossary

| Term | Definition |
|------|------------|
| FR | Functional Requirement (FR-01..FR-10) — what the system does |
| NFR | Non-Functional Requirement (NFR-01..NFR-12) — how the system does it |
| AC | Acceptance Criterion — one machine-citable, single-decision check |
| scope | API key permission level: `read` < `write` < `admin` |
| `shell=True` | Forbidden subprocess flag — exec form required instead |
| `selectinload` / `joinedload` | SQLAlchemy explicit eager-loading APIs |
| N+1 | Failure pattern: per-row follow-up query that scales linearly with rows |
| `pool_pre_ping` | SQLAlchemy flag that validates connections before use |
| `CancelledError` | `asyncio.CancelledError` — async-specific cancellation signal |
| `TaskGroup` | `asyncio.TaskGroup` (Python 3.11+) — structured concurrent tasks |
| rate bucket | Per-token token-bucket; capacity `TASKQ_RATE_BURST`, refill `TASKQ_RATE_PER_SEC` |
| Alembic | SQLAlchemy's database migration tool |
| revision (v1/v2/v3) | Three sequential schema migrations; v3 includes a data-migration step |
| `downgrade` | Alembic term for the reverse of an `upgrade` revision |
| RFC 7807 | "Problem Details for HTTP APIs" — defines `application/problem+json` |
| `problem+json` | Error-body media type defined by RFC 7807 |
| `correlation_id` | Per-request trace id present in body and `X-Correlation-Id` header |
| SBOM | Software Bill of Materials — per-dep name/version/license/direct|transitive |
| CRG | Code-Review-Graph — structural code-knowledge graph used by Gate architecture scoring |
| mutmut | Mutation-testing framework for Python |
| bandit | Static security linter for Python |
| gitleaks | Secret-string scanner |
| pip-licenses | Dependency license enumerator |
| import-linter | Static analyser enforcing layered import contracts via `.importlinter` |
| pytest-benchmark | Micro-benchmark framework; latencies read from `.sessi-work/benchmark_report.json` |
| ASGI / ASGITransport | Async Server Gateway Interface; `httpx.ASGITransport(app)` drives the app in-process |
| scope hierarchy | `read` ⊂ `write` � `admin` — a key with a broader scope satisfies narrower endpoints |
| `crg_cohesion_healthy` | Per-project CRG cohesion floor (default 0.3); **must not be lowered** to make the project pass |

---

## 10. FR Block (machine-readable)

<!-- FR:START -->
```json
{
  "version": "1.0",
  "created_at": "2026-08-22",
  "phase": 1,
  "project": "taskq-api",
  "functional_requirements": [
    {
      "id": "FR-01",
      "description": "Task resource CRUD API — POST/GET/LIST/DELETE under /v1/tasks; cursor pagination; 422/404/409 error mapping per SPEC.md §3 FR-01",
      "implementation_functions": [
        "create_task_handler",
        "get_task_handler",
        "list_tasks_handler",
        "delete_task_handler"
      ],
      "verification_method": "pytest integration suite driving httpx.ASGITransport(app) with [FR-01]-tagged docstrings; AC-1.1..AC-1.10"
    },
    {
      "id": "FR-02",
      "description": "Task execution endpoint — POST /v1/tasks/{id}/run returns 202; asyncio.create_subprocess_exec with shlex.split (shell=True forbidden); result rows in task_results; GET /v1/tasks/{id}/runs history",
      "implementation_functions": [
        "run_task_handler",
        "list_task_runs_handler",
        "execute_subprocess_runner"
      ],
      "verification_method": "AC-2.1..AC-2.5; subprocess integration tests with timeout-kill assertion; SPEC.md §8 #16 (grep)"
    },
    {
      "id": "FR-03",
      "description": "API Key authentication — X-API-Key header; SHA-256 hash storage; hmac.compare_digest constant-time compare; plaintext printed once on key create; revoked_at invalidates key; /healthz and /readyz unauthenticated",
      "implementation_functions": [
        "authenticate_request_dependency",
        "create_api_key_cli_command"
      ],
      "verification_method": "AC-3.1..AC-3.6; SPEC.md §8 #5 (401), #18 (hashed storage)"
    },
    {
      "id": "FR-04",
      "description": "Scope authorization — read < write < admin hierarchical; 403 + problem+json with no existence leak; single-dependency enforcement verified by test",
      "implementation_functions": [
        "require_scope_dependency"
      ],
      "verification_method": "AC-4.1..AC-4.3; SPEC.md §8 #6 (403 + no leak)"
    },
    {
      "id": "FR-05",
      "description": "Rate limiting — per-token token bucket (TASKQ_RATE_BURST / TASKQ_RATE_PER_SEC); 429 + Retry-After; DB-backed state with row-level lock; /healthz and /readyz exempt",
      "implementation_functions": [
        "rate_limit_dependency",
        "consume_token_bucket_repository"
      ],
      "verification_method": "AC-5.1..AC-5.4; SPEC.md §8 #9 (429 + Retry-After)"
    },
    {
      "id": "FR-06",
      "description": "Persistence + transaction boundaries — repository-layer-only data access; one Session per request with context manager commit/rollback; no string-concatenated SQL; selectinload/joinedload explicit eager loading (N+1 forbidden); pool_size + pool_pre_ping",
      "implementation_functions": [
        "transactional_session_context_manager",
        "task_repository",
        "api_key_repository",
        "rate_bucket_repository",
        "task_result_repository"
      ],
      "verification_method": "AC-6.1..AC-6.5; SPEC.md §8 #14 (N+1), #17 (SQL concat grep)"
    },
    {
      "id": "FR-07",
      "description": "Schema migration — Alembic v1 (base tables) → v2 (tags many-to-many + unique index) → v3 (tasks.result_json → task_results with data migration); every step reversible; round-trip column-byte-identical",
      "implementation_functions": [
        "alembic_env_py",
        "revision_v1_base_tables",
        "revision_v2_tags_and_index",
        "revision_v3_data_migration_to_task_results"
      ],
      "verification_method": "AC-7.1..AC-7.7; SPEC.md §8 #12 (round-trip), #13 (downgrade base)"
    },
    {
      "id": "FR-08",
      "description": "Async executor — asyncio.TaskGroup background; TASKQ_MAX_CONCURRENT cap; TASKQ_DRAIN_TIMEOUT graceful drain (mark interrupted on overrun); asyncio.wait_for timeout + process.kill/await wait; CancelledError propagation (no except Exception swallow)",
      "implementation_functions": [
        "async_subprocess_runner",
        "task_group_orchestrator",
        "graceful_shutdown_drain"
      ],
      "verification_method": "AC-8.1..AC-8.4; SPEC.md §8 #25 (no orphans)"
    },
    {
      "id": "FR-09",
      "description": "Health checks + observability — /healthz (process alive, no auth); /readyz (DB reachable AND alembic current == head, 503 on either failure with detail, no auth); /v1/metrics (admin scope; task counts by status, latency percentiles, rate-limit rejections)",
      "implementation_functions": [
        "healthz_endpoint",
        "readyz_endpoint",
        "v1_metrics_endpoint"
      ],
      "verification_method": "AC-9.1..AC-9.3; SPEC.md §8 #10/#11 (503 detail)"
    },
    {
      "id": "FR-10",
      "description": "Error contract RFC 7807 — application/problem+json on every non-2xx; body fields type/title/status/detail/instance/correlation_id; detail excludes SQL/stack/path/schema; X-Correlation-Id header mirrors correlation_id; HTTP status map per SPEC.md §7",
      "implementation_functions": [
        "problem_json_exception_handler",
        "correlation_id_middleware"
      ],
      "verification_method": "AC-10.1..AC-10.5; SPEC.md §8 #19 (500 body leak)"
    }
  ],
  "non_functional_requirements": [
    {
      "id": "NFR-01",
      "type": "performance",
      "description": "Performance + query efficiency — GET /v1/tasks/{id} p95 < 30ms at 10k rows; GET /v1/tasks?limit=50 p95 < 80ms at 10k rows; list endpoint SQL statement count constant ≤ 4 across {1, 100, 1000, 10000} (variance 0); pytest-benchmark measurement",
      "test_method": "AC-N1.1..AC-N1.4; SPEC.md §11 monitoring thresholds; SQLAlchemy event listener"
    },
    {
      "id": "NFR-02",
      "type": "security",
      "description": "HTTP + data-layer security — no shell=True / eval( / exec( in src/; no string-concatenated SQL; hashed API keys + hmac.compare_digest; 403 no existence leak; error body no stack/SQL/path; CORS deny-by-default + TASKQ_CORS_ORIGINS allowlist; bandit 0 HIGH / 0 MEDIUM",
      "test_method": "AC-N2.1..AC-N2.7; SPEC.md §8 #16/#17/#18/#19/#21/#23; bandit + grep gates"
    },
    {
      "id": "NFR-03",
      "type": "reliability",
      "description": "Error handling, transactions, async correctness — explicit per-request transaction boundary via context manager (commit/rollback); no bare except: or except Exception: pass; asyncio.CancelledError must re-raise; DB failure → /readyz 503 with detail (no silent retry); timeout kills subprocess; migration rollback on failure",
      "test_method": "AC-N3.1..AC-N3.6; ast-error-handling scanner + targeted integration tests"
    },
    {
      "id": "NFR-04",
      "type": "security",
      "description": "Sensitive data redaction — pattern (sk-[A-Za-z0-9_-]{8,}|token=\\S+|Bearer\\s+\\S+|postgres(ql)?://[^\\s]+) replaced with [REDACTED] in stdout_tail/stderr_tail/logs/error bodies before write/emit; database connection string (incl. password) absent from logs/errors/metrics; API key plaintext printed only once on key create",
      "test_method": "AC-N4.1..AC-N4.3; SPEC.md §8 #20"
    },
    {
      "id": "NFR-05",
      "type": "documentation",
      "description": "Documentation coverage — every public function/class has a docstring referencing [FR-XX] or [NFR-XX]; coverage 100%; every API endpoint has summary + description in OpenAPI (/openapi.json)",
      "test_method": "AC-N5.1..AC-N5.2; ast-docstrings scanner; OpenAPI schema assertion"
    },
    {
      "id": "NFR-06",
      "type": "layering",
      "description": "Architecture layer contract — .importlinter declares api > service > repository > models (upper may import lower, lower may not import upper); config + errors as independence modules; forbidden contract bans sqlalchemy imports outside repository; lint-imports exit 0; no .importlinter removal / ignore_imports wildcards / contract downgrade to pass",
      "test_method": "AC-N6.1..AC-N6.4; SPEC.md §8 #21; lint-imports"
    },
    {
      "id": "NFR-07",
      "type": "licensing",
      "description": "Dependency + license compliance — requirements.txt ==-pinned; requirements.lock fully pins transitives; allowed licenses MIT / BSD-2-Clause / BSD-3-Clause / Apache-2.0 / PSF (others forbidden); full-tree scan via pip-licenses --format=json --with-system; SBOM artifact with name/version/license/direct|transitive",
      "test_method": "AC-N7.1..AC-N7.4; SPEC.md §8 #22"
    },
    {
      "id": "NFR-08",
      "type": "mutation",
      "description": "Mutation testing — .methodology/harness_config.json sets features.mutation_testing: true; mutation score ≥ 70 over services + repositories layers (NFR-06 roles); scope-restriction rationale recorded in harness_config.json (runtime-budget)",
      "test_method": "AC-N8.1..AC-N8.3; SPEC.md §8 #24; mutmut (framework-managed)"
    },
    {
      "id": "NFR-09",
      "type": "testability",
      "description": "Verification honesty (zero-skip) — no pytest.skip / skipif / xfail / assertion-free stub on any FR/NFR test; pytest skipped count = 0; every test ≥ 1 assert (zero_assert == 0); no --ignore / -k / --deselect / collect_ignore / testpaths removal; FR-07 migration tested against real SQLite file (not in-memory mock); no skip-on-difficulty; TRACEABILITY_MATRIX.md VERIFIED only after test runs and passes",
      "test_method": "AC-N9.1..AC-N9.7; ast-assertions + pytest invocation"
    },
    {
      "id": "NFR-10",
      "type": "integration",
      "description": "Integration coverage — tests/integration/ line coverage ≥ 80% over src/; integration tests driven via httpx.AsyncClient(transport=ASGITransport(app)) (no direct handler calls); scope covers full CRUD chain, each error code 401/403/404/409/422/429/503 ≥ once, migration round-trip, rate-limit trigger+recovery, graceful drain",
      "test_method": "AC-N10.1..AC-N10.3; pytest-cov-integration"
    },
    {
      "id": "NFR-11",
      "type": "maintainability",
      "description": "Readability — project MI (LLOC-weighted) ≥ 80; single function CC ≤ 10; single file ≤ 400 lines; single directory ≤ 15 files; each API handler ≤ 40 lines (business logic descends to service layer)",
      "test_method": "AC-N11.1..AC-N11.3; radon-mi / readability-v2"
    },
    {
      "id": "NFR-12",
      "type": "verifiability",
      "description": "System verification target — Makefile verify-system chains (1) alembic upgrade head, (2) full test suite, (3) service start + /healthz / /readyz smoke, (4) alembic downgrade base then upgrade head (round-trip); exits 0 and prints verify-system: PASS to stdout",
      "test_method": "AC-N12.1..AC-N12.2; SPEC.md §8 #27"
    }
  ]
}
```
<!-- FR:END -->

Note on NFR `type:` mapping: SPEC.md `dimension:` keys map to the SAB
NFR vocabulary as follows (every value is a member of
`harness/core/quality_gate/sab_parser.ALL_NFR_TYPES`):
`performance → performance`,
`security → security`,
`error_handling → reliability`,
`documentation → documentation`,
`architecture_constraints → layering`,
`license_compliance → licensing`,
`mutation_testing → mutation`,
`test_assertion_quality → testability`,
`integration_coverage → integration`,
`readability → maintainability`,
`execute_verification_target → verifiability`.

---

*End of SRS — taskq-api v1.0.0 (10 FR / 12 NFR / 12 env) — 2026-08-22.*
