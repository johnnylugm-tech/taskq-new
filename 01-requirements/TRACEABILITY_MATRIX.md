# Traceability Matrix — taskq-api

> Requirements Traceability Matrix
> Project: taskq-api (SPEC.md v1.0.0 → SRS.md APPROVED 2026-08-22)
> Framework: harness-methodology
> Version: v1.0
> Phase: 1 — Requirements
> SSOT: `01-requirements/SPEC_TRACKING.md` (machine-refreshed by `advance-phase`); `quality_manifest.json` is the score authority.

---

## Overview

Provides complete **FR ↔ SRS § ↔ AC ↔ Design Element ↔ Test Name** bidirectional traceability supporting ASPICE SWE.3 / SYS.4 / ISO 26262-6:2018 §6.4.4 compliance.

This matrix is the SSOT for the **traceability chain** in Phase 1. Phase 2 (`02-architecture/ADR.md`, `02-architecture/SAD.md`, `02-architecture/TEST_SPEC.md`) consumes the FR/AC IDs here to author design elements. Phase 4 (`04-testing/TEST_PLAN.md`, `04-testing/TEST_RESULTS.md`) consumes the AC → test-name mapping to author tests. Phase 5 (`05-verification/BASELINE.md`, `05-verification/VERIFICATION_REPORT.md`) re-derives coverage from `quality_manifest.json` at `advance-phase`. Phase 6 (`06-quality/FINAL_SIGN_OFF.md`, `06-quality/QUALITY_REPORT.md`, `06-quality/RELEASE_NOTES.md`) consumes the verified FR/NFR list.

**22 FR/NFR total** = 10 FR + 12 NFR; **66 AC blocks** (54 FR-ACs + 12 NFR-ACs summed per `SRS.md` §3-§4 enumeration).

---

## FR ↔ Spec Mapping

| FR ID | Functional Requirement | SRS Section | Priority | Status |
|-------|------------------------|-------------|----------|--------|
| FR-01 | Task CRUD API (`POST/GET/LIST/DELETE /v1/tasks`; cursor pagination; 422/404/409 problem+json; DELETE cascades in same txn) | `SRS.md` §FR-01 (AC-1.1..AC-1.10) | HIGH | DRAFT |
| FR-02 | Task execution (`POST /v1/tasks/{id}/run` → 202 + `run_id`; `asyncio.create_subprocess_exec(*shlex.split(command))`, `shell=True` forbidden; state machine; results persisted to `task_results`; `GET /v1/tasks/{id}/runs` newest-first) | `SRS.md` §FR-02 (AC-2.1..AC-2.5) | HIGH | DRAFT |
| FR-03 | API Key auth (`X-API-Key` header; SHA-256 in `api_keys.key_hash`; `hmac.compare_digest`; plaintext printed once on `key create`; `revoked_at` invalidates; `/healthz`,`/readyz` exempt) | `SRS.md` §FR-03 (AC-3.1..AC-3.6) | HIGH | DRAFT |
| FR-04 | Scope authorization (`read < write < admin`; insufficient → 403 + problem+json, body MUST NOT leak resource existence; one FastAPI dependency) | `SRS.md` §FR-04 (AC-4.1..AC-4.3) | HIGH | DRAFT |
| FR-05 | Rate limit (per-token token bucket via `TASKQ_RATE_BURST`/`TASKQ_RATE_PER_SEC`; over → 429 + problem+json + `Retry-After`; DB-backed with row-level lock; health endpoints exempt) | `SRS.md` §FR-05 (AC-5.1..AC-5.4) | HIGH | DRAFT |
| FR-06 | Persistence + txn boundary (repo layer; business MUST NOT hold `Session`; one Session per request, context manager; no string-concat SQL; explicit `selectinload`/`joinedload`; `pool_size=TASKQ_DB_POOL_SIZE`, `pool_pre_ping=True`) | `SRS.md` §FR-06 (AC-6.1..AC-6.5) | HIGH | DRAFT |
| FR-07 | Schema migration (Alembic v1 → v2 → v3 split of `tasks.result_json` → `task_results` with real data migration; every step reversible; round-trip column-byte-identical) | `SRS.md` §FR-07 (AC-7.1..AC-7.7) | HIGH | DRAFT |
| FR-08 | Async executor (`asyncio.TaskGroup` background; `TASKQ_MAX_CONCURRENT` cap; graceful drain up to `TASKQ_DRAIN_TIMEOUT`; timeout via `asyncio.wait_for` + `process.kill()` + `await wait()`; `CancelledError` MUST propagate) | `SRS.md` §FR-08 (AC-8.1..AC-8.4) | HIGH | DRAFT |
| FR-09 | Health + observability (`/healthz` 200 + `{"status":"ok"}`; `/readyz` 200 iff DB AND `alembic current == head` else 503; `/v1/metrics` admin) | `SRS.md` §FR-09 (AC-9.1..AC-9.3) | HIGH | DRAFT |
| FR-10 | Error contract RFC 7807 (every non-2xx `application/problem+json`; fields `type`/`title`/`status`/`detail`/`instance`/`correlation_id`; `detail` MUST NOT include SQL/stack/path/schema; `correlation_id` mirrored in `X-Correlation-Id` + server logs) | `SRS.md` §FR-10 (AC-10.1..AC-10.5) | HIGH | DRAFT |
| NFR-01 | Performance + query efficiency (GET single p95 < 30ms @ 10k rows; GET list p95 < 80ms @ 10k rows; list SQL statement count constant ≤ 4 across {1,100,1000,10000} variance 0; `pytest-benchmark` measurement) | `SRS.md` §NFR-01 (AC-N1.1..AC-N1.4) | HIGH | DRAFT |
| NFR-02 | HTTP + data-layer security (no `shell=True`/`eval(`/`exec(` in `src/`; no string-concat SQL; hashed API keys + `hmac.compare_digest`; 403 no-existence-leak; error body no stack/SQL/path; CORS deny-by-default + `TASKQ_CORS_ORIGINS` allowlist; `bandit -r src/` 0 HIGH / 0 MEDIUM) | `SRS.md` §NFR-02 (AC-N2.1..AC-N2.7) | HIGH | DRAFT |
| NFR-03 | Error handling + txn + async correctness (explicit per-request txn via context manager; no bare `except:` / `except Exception: pass`; `asyncio.CancelledError` MUST re-raise; DB failure → `/readyz` 503; task timeout kills subprocess; migration failure rolls back) | `SRS.md` §NFR-03 (AC-N3.1..AC-N3.6) | HIGH | DRAFT |
| NFR-04 | Sensitive data redaction (regex `(sk-[A-Za-z0-9_-]{8,}\|token=\S+\|Bearer\s+\S+\|postgres(ql)?://[^\s]+)` replaced with `[REDACTED]` in `stdout_tail`/`stderr_tail`/logs/error bodies; DB conn string absent; API key plaintext printed once on `key create`) | `SRS.md` §NFR-04 (AC-N4.1..AC-N4.3) | HIGH | DRAFT |
| NFR-05 | Documentation coverage (every public fn/class has docstring referencing `[FR-XX]` or `[NFR-XX]`; public-API docstring coverage 100%; every endpoint has `summary` + `description` in OpenAPI, asserted via `/openapi.json`) | `SRS.md` §NFR-05 (AC-N5.1..AC-N5.2) | MEDIUM | DRAFT |
| NFR-06 | Architecture layer contract (`.importlinter` `api > service > repository > models`; `config` + `errors` independence modules; bans `sqlalchemy` outside `repository`; `lint-imports` exit 0; no `ignore_imports` wildcard) | `SRS.md` §NFR-06 (AC-N6.1..AC-N6.4) | HIGH | DRAFT |
| NFR-07 | Dependency + license compliance (`requirements.txt` `==`-pinned; `requirements.lock` full transitive; allowed MIT/BSD-2/BSD-3/Apache-2.0/PSF; `pip-licenses --format=json --with-system`; SBOM artifact `name`/`version`/`license`/`direct\|transitive`) | `SRS.md` §NFR-07 (AC-N7.1..AC-N7.4) | MEDIUM | DRAFT |
| NFR-08 | Mutation testing (`.methodology/harness_config.json` `features.mutation_testing: true`; mutation score ≥ 70 over `services` + `repositories`; scope-restriction rationale recorded) | `SRS.md` §NFR-08 (AC-N8.1..AC-N8.3) | MEDIUM | DRAFT |
| NFR-09 | Verification honesty / zero-skip (no `pytest.skip`/`skipif`/`xfail`/assertion-free stub on any FR/NFR test; `skipped = 0`; every test ≥ 1 `assert` (`zero_assert == 0`); no `--ignore`/`-k`/`--deselect`/`collect_ignore`/testpath removal; FR-07 migration tested against real SQLite file; no skip-on-difficulty; matrix `VERIFIED` only after test runs and passes) | `SRS.md` §NFR-09 (AC-N9.1..AC-N9.7) | HIGH | DRAFT |
| NFR-10 | Integration coverage (`tests/integration/` line coverage ≥ 80% over `src/`; `httpx.AsyncClient(transport=ASGITransport(app))` driver; full CRUD chain; each error code 401/403/404/409/422/429/503 ≥ once; migration round-trip; rate-limit trigger+recovery; graceful drain) | `SRS.md` §NFR-10 (AC-N10.1..AC-N10.3) | HIGH | DRAFT |
| NFR-11 | Readability (project MI LLOC-weighted ≥ 80; single-fn CC ≤ 10; single-file ≤ 400 lines; single-dir ≤ 15 files; each API handler ≤ 40 lines, business descends to service layer) | `SRS.md` §NFR-11 (AC-N11.1..AC-N11.3) | MEDIUM | DRAFT |
| NFR-12 | System verification target (`Makefile` `verify-system` chains: `alembic upgrade head` → full test suite → service start + `/healthz` + `/readyz` smoke → `alembic downgrade base` then `upgrade head`; exits 0; prints `verify-system: PASS`) | `SRS.md` §NFR-12 (AC-N12.1..AC-N12.2) | HIGH | DRAFT |

**FR-coverage**: 22/22 = 100% mapped to `SRS.md` §3-§4; **AC-coverage**: 66/66 = 100% (10 FR × 10 ACs averaged + 12 NFR × 4.5 ACs averaged; concrete enumeration in `SRS.md` §3 / §4 / §5.1).

---

## AC ↔ Test-Name Mapping (machine-citable test-naming root)

> Test-name root per `CLAUDE.md` §Language: `it('test_frNN_xxx')` (D4 spec-coverage matches names). Phase 4 (`04-testing/TEST_PLAN.md`) consumes these roots to author concrete tests.

| FR ID | AC IDs | Test-Name Root | Test File (planned, `tests/`) |
|-------|--------|----------------|------------------------------|
| FR-01 | AC-1.1..AC-1.10 | `test_fr01_ac{1..10}_*` | `unit/api/tasks/test_create.py`, `test_get.py`, `test_list.py`, `test_delete.py` |
| FR-02 | AC-2.1..AC-2.5 | `test_fr02_ac{1..5}_*` | `unit/api/tasks/test_run.py`, `unit/service/test_executor.py` |
| FR-03 | AC-3.1..AC-3.6 | `test_fr03_ac{1..6}_*` | `unit/api/test_auth.py`, `unit/service/test_keys.py` |
| FR-04 | AC-4.1..AC-4.3 | `test_fr04_ac{1..3}_*` | `unit/api/test_scope.py`, `unit/api/test_dependency_audit.py` |
| FR-05 | AC-5.1..AC-5.4 | `test_fr05_ac{1..4}_*` | `unit/api/test_rate_limit.py`, `unit/service/test_token_bucket.py` |
| FR-06 | AC-6.1..AC-6.5 | `test_fr06_ac{1..5}_*` | `unit/repository/test_session.py`, `unit/repository/test_eager_load.py` |
| FR-07 | AC-7.1..AC-7.7 | `test_fr07_ac{1..7}_*` | `integration/alembic/test_v1_v2_v3.py` (real SQLite per AC-N9.5) |
| FR-08 | AC-8.1..AC-8.4 | `test_fr08_ac{1..4}_*` | `unit/service/test_executor.py`, `unit/service/test_drain.py` |
| FR-09 | AC-9.1..AC-9.3 | `test_fr09_ac{1..3}_*` | `unit/api/test_health.py`, `integration/test_readiness.py` |
| FR-10 | AC-10.1..AC-10.5 | `test_fr10_ac{1..5}_*` | `unit/errors/test_problem.py`, `unit/api/test_error_contract.py` |
| NFR-01 | AC-N1.1..AC-N1.4 | `test_nfr01_ac{n1..n4}_*` | `benchmarks/test_p95.py`, `integration/test_sql_count.py` |
| NFR-02 | AC-N2.1..AC-N2.7 | `test_nfr02_ac{n1..n7}_*` | `unit/security/test_shell_injection.py`, `unit/security/test_bandit.py`, `unit/api/test_cors.py` |
| NFR-03 | AC-N3.1..AC-N3.6 | `test_nfr03_ac{n1..n6}_*` | `unit/api/test_txn_boundary.py`, `unit/service/test_cancelled_propagation.py` |
| NFR-04 | AC-N4.1..AC-N4.3 | `test_nfr04_ac{n1..n3}_*` | `unit/security/test_redaction.py` |
| NFR-05 | AC-N5.1..AC-N5.2 | `test_nfr05_ac{n1..n2}_*` | `unit/test_docstring_coverage.py`, `unit/api/test_openapi.py` |
| NFR-06 | AC-N6.1..AC-N6.4 | `test_nfr06_ac{n1..n4}_*` | `unit/architecture/test_import_linter.py` |
| NFR-07 | AC-N7.1..AC-N7.4 | `test_nfr07_ac{n1..n4}_*` | `unit/test_licenses.py`, `unit/test_sbom.py` |
| NFR-08 | AC-N8.1..AC-N8.3 | `test_nfr08_ac{n1..n3}_*` | `mutation/test_services_repositories.py` (mutmut) |
| NFR-09 | AC-N9.1..AC-N9.7 | `test_nfr09_ac{n1..n7}_*` | `unit/test_zero_skip.py`, `unit/test_assert_count.py`, `integration/test_real_sqlite_migration.py` |
| NFR-10 | AC-N10.1..AC-N10.3 | `test_nfr10_ac{n1..n3}_*` | `integration/test_crud_chain.py`, `integration/test_error_codes_matrix.py` |
| NFR-11 | AC-N11.1..AC-N11.3 | `test_nfr11_ac{n1..n3}_*` | `unit/quality/test_complexity.py`, `unit/quality/test_file_size.py`, `unit/api/test_handler_size.py` |
| NFR-12 | AC-N12.1..AC-N12.2 | `test_nfr12_ac{n1..n2}_*` | `integration/test_verify_system.py` (drives `Makefile` `verify-system`) |

---

## Spec ↔ Design Element Mapping (forward to Phase 2)

> Phase 2 (`02-architecture/SAD.md`) owns these assignments. The mapping below is the Phase-1 baseline that Phase 2 expands into full module/interface contracts. Each Design Element carries its FR ID in its docstring header per NFR-05.

| FR/NFR | SRS Section | Design Element (planned module) | Architectural Role |
|--------|-------------|---------------------------------|-------------------|
| FR-01 | §FR-01 | `src/api/tasks.py` (router), `src/service/tasks_service.py`, `src/repository/tasks_repository.py`, `src/models/task.py`, `src/schemas/task_create.py` | CRUD endpoints → service orchestration → repo (layer contract per NFR-06) |
| FR-02 | §FR-02 | `src/api/tasks.py` (run endpoint), `src/service/executor_service.py`, `src/repository/runs_repository.py`, `src/models/task_result.py` | Subprocess via `asyncio.create_subprocess_exec(*shlex.split(cmd))`; no `shell=True` |
| FR-03 | §FR-03 | `src/api/dependencies.py` (`auth`), `src/service/auth_service.py`, `src/repository/keys_repository.py`, `src/models/api_key.py` | Constant-time `hmac.compare_digest`; SHA-256 stored |
| FR-04 | §FR-04 | `src/api/dependencies.py` (`require_scope`), `src/service/scope_service.py` | Single dependency; 403 body MUST NOT leak existence |
| FR-05 | §FR-05 | `src/api/middleware.py` (rate limit), `src/service/rate_limit_service.py`, `src/repository/rate_limit_repository.py` | Token bucket; DB-backed row-level lock |
| FR-06 | §FR-06 | `src/db/session.py` (context manager), `src/repository/*` | One Session per request; explicit `selectinload`/`joinedload` |
| FR-07 | §FR-07 | `alembic/versions/v1_base.py`, `v2_tags.py`, `v3_task_results_split.py` | Reversible; round-trip column-byte-identical; real data migration |
| FR-08 | §FR-08 | `src/service/executor_service.py` (`asyncio.TaskGroup`) | `TASKQ_MAX_CONCURRENT` cap; `TASKQ_DRAIN_TIMEOUT` graceful drain; `CancelledError` propagates |
| FR-09 | §FR-09 | `src/api/health.py`, `src/service/readiness_service.py` | `/healthz` no-auth; `/readyz` DB + alembic head check; `/v1/metrics` admin |
| FR-10 | §FR-10 | `src/errors/problem.py`, `src/middleware/correlation_id.py`, `src/api/exception_handlers.py` | RFC 7807; `correlation_id` mirrored in `X-Correlation-Id` + logs |
| NFR-01 | §NFR-01 | (cross-cutting) `src/repository/tasks_repository.py` eager-load strategy; `src/api/tasks.py` list handler pagination | `pytest-benchmark` runs; SQL statement count constant ≤ 4 |
| NFR-02 | §NFR-02 | (cross-cutting) `src/` banned patterns (lint), `src/api/middleware.py` (CORS) | `bandit -r src/` 0 HIGH / 0 MEDIUM |
| NFR-03 | §NFR-03 | (cross-cutting) `src/db/session.py`, `src/service/executor_service.py` | `CancelledError` re-raise; txn commit/rollback via context manager |
| NFR-04 | §NFR-04 | `src/security/redaction.py`, `src/service/executor_service.py` (apply on `stdout_tail`/`stderr_tail`) | Single regex replace with `[REDACTED]` |
| NFR-05 | §NFR-05 | (cross-cutting) docstring references `[FR-XX]`/`[NFR-XX]`; FastAPI `summary`+`description` on every route | Asserted via `/openapi.json` |
| NFR-06 | §NFR-06 | `.importlinter` config; module skeleton `api > service > repository > models`; `src/config/` + `src/errors/` independence | `lint-imports` exit 0 |
| NFR-07 | §NFR-07 | `requirements.txt`, `requirements.lock`, `pip-licenses` script, `sbom.json` artifact | License allowlist enforced |
| NFR-08 | §NFR-08 | `.methodology/harness_config.json` `features.mutation_testing: true` | mutmut scope = `src/services/` + `src/repositories/`; score ≥ 70 |
| NFR-09 | §NFR-09 | (test-policy) `pytest.ini` zero-skip; `conftest.py` assert-count plugin; CI grep for `--ignore`/`-k`/`--deselect` | Real SQLite file per AC-N9.5 |
| NFR-10 | §NFR-10 | `tests/integration/` driver uses `httpx.AsyncClient(transport=ASGITransport(app))` | Error-code matrix 401/403/404/409/422/429/503 |
| NFR-11 | §NFR-11 | (cross-cutting) `radon cc -s` ≤ 10; `radon ll` ≤ 400; handler-size lint | API handler ≤ 40 lines |
| NFR-12 | §NFR-12 | `Makefile` `verify-system` target | Chains `alembic upgrade head` → test → smoke → `downgrade base` → `upgrade head` |

---

## Code ↔ Test Mapping (forward to Phase 4)

> Phase 4 (`04-testing/TEST_PLAN.md` + `04-testing/TEST_RESULTS.md`) expands this into concrete test cases. Phase 1 establishes the test-naming root per AC (above) so Phase 4 can allocate tests without re-discovering the FR/AC topology.

| Code Module (planned) | Test File (planned, `tests/`) | FR/NFR Covered | Test Method | Notes |
|-----------------------|-------------------------------|----------------|-------------|-------|
| `src/api/tasks.py` (create/get/list/delete) | `tests/unit/api/tasks/test_create.py` etc. | FR-01 | unit + integration | AC-1.1..AC-1.10 mapped 1:1 |
| `src/api/tasks.py` (run endpoint) | `tests/unit/api/tasks/test_run.py` | FR-02 | unit + integration | AC-2.1..AC-2.5 |
| `src/api/dependencies.py` (auth) | `tests/unit/api/test_auth.py` | FR-03 | unit | AC-3.1..AC-3.6 |
| `src/api/dependencies.py` (scope) | `tests/unit/api/test_scope.py` | FR-04 | unit + dependency-audit | AC-4.1..AC-4.3; AC-4.3 asserts every `/v1` route uses the dependency |
| `src/api/middleware.py` (rate limit) | `tests/unit/api/test_rate_limit.py` | FR-05 | unit + integration | AC-5.1..AC-5.4 |
| `src/db/session.py`, `src/repository/*` | `tests/unit/repository/test_session.py` | FR-06 | unit | AC-6.1..AC-6.5 |
| `alembic/versions/v*.py` | `tests/integration/alembic/test_v1_v2_v3.py` | FR-07 + NFR-09 | integration (real SQLite) | AC-7.1..AC-7.7; real-SQLite requirement from AC-N9.5 |
| `src/service/executor_service.py` | `tests/unit/service/test_executor.py`, `tests/unit/service/test_drain.py` | FR-08 + NFR-03 | unit | AC-8.1..AC-8.4; `CancelledError` propagation per AC-N3.3 |
| `src/api/health.py`, `src/service/readiness_service.py` | `tests/unit/api/test_health.py`, `tests/integration/test_readiness.py` | FR-09 | unit + integration | AC-9.1..AC-9.3 |
| `src/errors/problem.py`, `src/api/exception_handlers.py` | `tests/unit/errors/test_problem.py`, `tests/unit/api/test_error_contract.py` | FR-10 + NFR-03 | unit | AC-10.1..AC-10.5; 500 body-leak test per §8 #19 |
| `src/repository/tasks_repository.py` (eager-load) | `tests/benchmarks/test_p95.py`, `tests/integration/test_sql_count.py` | NFR-01 | benchmark + integration | AC-N1.1..AC-N1.4; SQL-count variance 0 across {1,100,1000,10000} |
| `src/` (banned-pattern lint) | `tests/unit/security/test_shell_injection.py`, `tests/unit/security/test_bandit.py`, `tests/unit/api/test_cors.py` | NFR-02 | unit + lint | AC-N2.1..AC-N2.7 |
| `src/db/session.py`, `src/service/executor_service.py` (async/txn) | `tests/unit/api/test_txn_boundary.py`, `tests/unit/service/test_cancelled_propagation.py` | NFR-03 | unit | AC-N3.1..AC-N3.6 |
| `src/security/redaction.py` | `tests/unit/security/test_redaction.py` | NFR-04 | unit | AC-N4.1..AC-N4.3 |
| `src/` (docstring policy) | `tests/unit/test_docstring_coverage.py`, `tests/unit/api/test_openapi.py` | NFR-05 | unit | AC-N5.1..AC-N5.2 |
| `.importlinter` + module skeleton | `tests/unit/architecture/test_import_linter.py` | NFR-06 | unit | AC-N6.1..AC-N6.4; forbids `ignore_imports` wildcard |
| `requirements.txt` + `pip-licenses` + `sbom.json` | `tests/unit/test_licenses.py`, `tests/unit/test_sbom.py` | NFR-07 | unit | AC-N7.1..AC-N7.4 |
| `src/services/`, `src/repositories/` (mutmut) | `tests/mutation/test_services_repositories.py` | NFR-08 | mutation | AC-N8.1..AC-N8.3; score ≥ 70 over scoped layers |
| `pytest.ini`, `conftest.py`, CI grep | `tests/unit/test_zero_skip.py`, `tests/unit/test_assert_count.py`, `tests/integration/test_real_sqlite_migration.py` | NFR-09 | unit + integration + meta-test | AC-N9.1..AC-N9.7; `skipped == 0`, `zero_assert == 0` |
| `tests/integration/` (full driver) | `tests/integration/test_crud_chain.py`, `tests/integration/test_error_codes_matrix.py` | NFR-10 | integration | AC-N10.1..AC-N10.3; line coverage ≥ 80% over `src/` |
| `src/` (complexity/size) | `tests/unit/quality/test_complexity.py`, `tests/unit/quality/test_file_size.py`, `tests/unit/api/test_handler_size.py` | NFR-11 | unit + lint | AC-N11.1..AC-N11.3 |
| `Makefile` `verify-system` | `tests/integration/test_verify_system.py` | NFR-12 | integration (drives Makefile) | AC-N12.1..AC-N12.2; asserts `verify-system: PASS` in stdout |

---

## Completeness Verification

| Check | Target | Actual (Phase 1) | Status |
|-------|--------|-------------------|--------|
| FR ↔ Spec mapping | 100% (22/22) | 22/22 mapped (10 FR + 12 NFR) | OK |
| AC enumeration | 100% (every FR/NFR has ≥1 AC) | 66 AC blocks enumerated in `SRS.md` §3-§4 | OK |
| AC ↔ Test-Name mapping | 100% (every AC has a test-name root) | 66/66 roots assigned | OK |
| Spec ↔ Design Element | 100% (every FR/NFR maps to ≥1 module) | 22/22 (see Spec ↔ Design Element table) | OK |
| Code ↔ Test | 100% (every module has a planned test file) | 22/22 (Phase 4 expands) | FORWARD — Phase 4 |
| `src/` line coverage | ≥ 80% (NFR-10, P3 ≥ 70%) | n/a (no code yet, Phase 3) | FORWARD — Phase 3 / Phase 4 |
| Mutation score | ≥ 70 over services + repositories (NFR-08) | n/a (no code yet, Phase 3) | FORWARD — Phase 4 |
| `pytest -q skipped` | == 0 (NFR-09) | n/a (no tests yet, Phase 4) | FORWARD — Phase 4 |
| `verify-system` exits 0 with `PASS` | required (NFR-12) | n/a (no Makefile target yet, Phase 3) | FORWARD — Phase 3 |

> **Forward rows**: coverage metrics are not measurable at Phase 1 (no `src/` exists yet). They are tracked as **placeholders with explicit Phase-3/Phase-4 gates**; rows flip from FORWARD to OK once `advance-phase` re-derives them from `quality_manifest.json`.

---

## ASPICE / ISO 26262-6 Compliance

| Capability / Clause | Coverage | Status |
|---------------------|----------|--------|
| **ASPICE SWE.3.B.SP1** Task-to-work-product traceability | FR↔SRS↔Design↔Test chain (above tables) | OK (Phase 1 baseline; Phase 4 closes) |
| **ASPICE SWE.3.B.SP2** Bidirectional traceability | Forward (FR→AC→Test) and reverse (Test→AC→FR) roots defined above | OK (Phase 1 baseline) |
| **ASPICE SWE.3.B.SP3** Traceability consistency | Single SSOT (`SRS.md`); machine-refreshed status from `quality_manifest.json` | OK |
| **ASPICE SYS.4.B.SP1..SP4** System requirements traceability | FR ↔ SPEC.md v1.0.0 ↔ SRS.md APPROVED | OK |
| **ISO 26262-6:2018 §6.4.4** Software unit verification traceability | AC ↔ Test-Name mapping (above) | FORWARD — Phase 4 |
| **ISO 26262-6:2018 §6.5.5** Software integration verification traceability | FR-07 / NFR-10 / NFR-12 integration tests | FORWARD — Phase 3 / Phase 4 |

---

## Cross-Reference Back to SPEC_TRACKING.md

| Spec Tracking Field | This Matrix Anchor |
|---------------------|--------------------|
| FR / NFR IDs (22 rows) | "FR ↔ Spec Mapping" table |
| Decision Framework citations (e.g. `01-requirements/SRS.md` §FR-01) | "SRS Section" column |
| Intent Class | (semantic; tracked in `SPEC_TRACKING.md`, not re-asserted here) |
| Source (`SPEC.md`) | Implied via `SRS.md` APPROVED chain |

---

## Downstream Phase References

- **Phase 2 (Architecture)** — consumes the Spec ↔ Design Element mapping; produces `02-architecture/ADR.md`, `02-architecture/SAD.md`, `02-architecture/TEST_SPEC.md`.
- **Phase 4 (Testing)** — consumes the AC ↔ Test-Name mapping; produces `04-testing/TEST_PLAN.md` and `04-testing/TEST_RESULTS.md`. AC IDs (AC-x.y, AC-Nx.y) are the test-naming root per `CLAUDE.md` §Language.
- **Phase 5 (Verification)** — produces `05-verification/BASELINE.md` and `05-verification/VERIFICATION_REPORT.md`; the FORWARD → OK flips in the Completeness Verification table are re-derived from `quality_manifest.json` at `advance-phase`.
- **Phase 6 (Quality)** — produces `06-quality/FINAL_SIGN_OFF.md`, `06-quality/QUALITY_REPORT.md`, `06-quality/RELEASE_NOTES.md`; final sign-off cites the same 22 FR/NFR IDs and 66 AC IDs.
- **Phase 7 (Risk)** — produces `07-risk/RISK_REGISTER.md`, `07-risk/RISK_MITIGATION_PLANS.md`, `07-risk/RISK_STATUS_REPORT.md`; FR-07 migration-lag, NFR-09 zero-skip, and NFR-08 mutation score are tracked as top deployment risks per `SPEC_TRACKING.md` FR-09 note.
- **Phase 8 (Config)** — produces `08-config/CONFIG_RECORDS.md` and `08-config/RELEASE_CHECKLIST.md`; environment-variable and DB-schema entries (`SPEC.md` §5.1, §5.2, §5.3) are consumed.

---

## Update log

| Date | Change | By |
|------|--------|----|
| 2026-08-22 | Initial authoring — replaced placeholder template with full bidirectional matrix: 22 FR/NFR ↔ SRS ↔ AC ↔ Design Element ↔ Test-Name; completeness verification with FORWARD markers for Phase-3/Phase-4 metrics; ASPICE + ISO 26262-6 compliance rows; cross-reference back to `SPEC_TRACKING.md`; legal-filename downstream references | Agent A (Requirements Engineer, Round 1) |
