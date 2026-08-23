# RISK MITIGATION PLANS — taskq-new

**Project:** taskq-new
**Generated:** 2026-08-24
**Phase:** P7 — Per-FR Delta (Risk Author)
**Companion:** `RISK_REGISTER.md`, `RISK_STATUS_REPORT.md`.

> **Scope.** Formal mitigation plans are required for every risk with
> `likelihood × impact ≥ 9` (selected HIGH-and-above per the register's 1–5 scale).
> Includes the single CRITICAL (R5) and every HIGH (R1, R3, R6, R9, R13, R14) plus
> MEDIUM items at the 9 threshold (R4, R7, R8, R10, R11, R15). R2 / R12 / R16 fall
> below the threshold and remain "monitored, no plan" in `RISK_STATUS_REPORT.md`.

---

## 0. Plan Index

| Plan ID  | Risk | Score | Tier     | Owner             | Target date  | Status      |
|----------|------|-------|----------|-------------------|--------------|-------------|
| PLAN-001 | R5   | 16    | CRITICAL | @backend-lead     | 2026-09-07   | IN PROGRESS |
| PLAN-002 | R1   | 12    | HIGH     | @migration-owner  | 2026-09-14   | OPEN        |
| PLAN-003 | R3   | 12    | HIGH     | @security-lead    | 2026-09-07   | MONITORED   |
| PLAN-004 | R6   | 12    | HIGH     | @api-lead         | 2026-08-31   | MONITORED   |
| PLAN-005 | R9   | 12    | HIGH     | @sre              | 2026-08-31   | MONITORED   |
| PLAN-006 | R13  | 12    | HIGH     | @qa-lead          | 2026-09-21   | OPEN        |
| PLAN-007 | R14  | 12    | HIGH     | @arch-lead        | 2026-09-21   | OPEN        |
| PLAN-008 | R4   | 9     | MEDIUM   | @api-lead         | 2026-09-07   | MONITORED   |
| PLAN-009 | R7   | 9     | MEDIUM   | @backend-lead     | 2026-08-31   | MONITORED   |
| PLAN-010 | R8   | 9     | MEDIUM   | @backend-lead     | 2026-09-07   | MONITORED   |
| PLAN-011 | R10  | 9     | MEDIUM   | @backend-lead     | 2026-09-14   | OPEN        |
| PLAN-012 | R11  | 9     | MEDIUM   | @release-mgr      | 2026-09-07   | MONITORED   |
| PLAN-013 | R15  | 9     | MEDIUM   | @arch-lead        | 2026-09-21   | OPEN        |

> **Owner placeholders** are role handles; substitute real names during P7 sign-off.
> **Target dates** assume a 2-week remediation window starting 2026-08-24.

---

## PLAN-001 — R5 N+1 Queries on Large Tables  *(CRITICAL)*

**Risk.** ORM lazy loading on list endpoints ⇒ per-row extra query ⇒ p95 SLA breach + pool exhaustion.

**Owner.** @backend-lead
**Target.** 2026-09-07
**Status.** IN PROGRESS

### Strategy
1. **Static detection.** Audit `repository/*.py` for `relationship` accesses inside list-returning functions; require `selectinload`/`joinedload`.
2. **Test enforcement.** Add a pytest plugin that captures SQLAlchemy query count per request and asserts `query_count ≤ N` for each list endpoint (N from §8 #14).
3. **Large-scale verification.** Provision ≥10k-row fixture; `pytest-benchmark` p95 must remain ≤ NFR-01 budget.
4. **Pool guard.** Semaphore on top of pool to enforce concurrent-open limit; `pool_pre_ping` already in place (R10 mitigation overlap).

### Acceptance Criteria
- [ ] All `api/*` list endpoints pass query-count assertion.
- [ ] p95 latency on 10k-row fixture ≤ NFR-01 budget.
- [ ] No regression in test suite (`make verify-system` exit 0).
- [ ] `gate_evidence/gate4/performance.json` re-run shows no N+1 regression.

### Rollback
If new query counts exceed budget, revert PR and re-design endpoint to a paginated cursor API.

---

## PLAN-002 — R1 v3 資料搬遷遺失資料  *(HIGH)*

**Risk.** Alembic v3 split (`result_json` → `task_results`) may lose columns or break round-trip.

**Owner.** @migration-owner
**Target.** 2026-09-14
**Status.** OPEN

### Strategy
1. **Round-trip parity test (mandatory).** Up → Down → Up on a real DB snapshot; row-by-row column comparison (FR-07 §8 #12).
2. **Sample set.** ≥ 100k rows including NULL / empty / 超長 / Unicode / boundary timestamps.
3. **Pre-commit gate.** Block any migration PR that lacks the `test_migration_roundtrip_*` assertion.
4. **Backup-first.** `pg_dump` → migrate → `pg_dump` → textual diff of canonical exports.

### Acceptance Criteria
- [ ] Round-trip test exists and is green on CI for ≥3 release branches.
- [ ] Mutation score of migration scripts ≥ 90% (subset mutmut).
- [ ] No data-loss incident in staging soak (≥7 days).

---

## PLAN-003 — R3 API Key 洩漏  *(HIGH)*

**Owner.** @security-lead
**Target.** 2026-09-07
**Status.** MONITORED (gates already green)

### Strategy
1. **Hash at rest.** Use `hashlib.scrypt` or argon2id; never store plaintext after issue.
2. **One-time display.** Plaintext returned ONLY in the `POST /keys` response body — never logged.
3. **Constant-time compare.** `hmac.compare_digest` on lookup.
4. **Continuous scan.** `secrets_scanning` gate blocks PRs containing key patterns; rotate any pre-existing cleartext.

### Acceptance Criteria
- [ ] No plaintext key in any storage location (`psql`, logs, backups).
- [ ] `gate_evidence/gate4/secrets_scanning.json` reports 0 hits.
- [ ] Pen-test confirms key cannot be retrieved from DB.

---

## PLAN-004 — R6 錯誤 Body 洩漏內部結構  *(HIGH)*

**Owner.** @api-lead
**Target.** 2026-08-31
**Status.** MONITORED

### Strategy
1. **RFC 7807 only.** All exception handlers return `application/problem+json` with fixed fields.
2. **Detail whitelist.** `detail` allowed values are short, user-safe strings only.
3. **Stack-trace suppression.** Production toggles `debug=false`; any stack-trace in response fails a contract test.

### Acceptance Criteria
- [ ] Negative-path tests cover 401/403/404/422/500.
- [ ] No PII / path / SQL appears in any `detail` (snapshot diff test).
- [ ] Gate 5 security pass with 0 stack-trace leaks.

---

## PLAN-005 — R9 部署後忘記跑 Migration  *(HIGH)*

**Owner.** @sre
**Target.** 2026-08-31
**Status.** MONITORED

### Strategy
1. **`/readyz` fail-closed.** Returns 503 if `alembic_version` ≠ expected revision.
2. **Startup gate.** Application process refuses to serve traffic until migration check passes.
3. **Runbook.** Deploy doc updated; on-call must re-run `/readyz` after `alembic upgrade`.

### Acceptance Criteria
- [ ] Drill: deploy old image with new schema → /readyz returns 503.
- [ ] Drill: deploy new image with old schema → /readyz returns 503.
- [ ] Runbook merged in `09-maintenance/`.

---

## PLAN-006 — R13 Mutation Score 72.1% 偏離 ≥80%  *(HIGH)*

**Owner.** @qa-lead
**Target.** 2026-09-21
**Status.** OPEN

### Strategy
1. **Triage survivors.** Export `.methodology/mutation_survivors.json` to a CSV grouped by file; tackle top-10 by impact first.
2. **Strengthen assertions.** For each survivor, add `pytest.raises`, `==`, `>=`, or boundary-check assertion.
3. **Remove dead code.** Where survivor reveals unreachable branch, delete the code (with PR justification).
4. **Re-run mutmut** on full scope; expect ≥80%.

### Acceptance Criteria
- [ ] Mutation score ≥ 80%.
- [ ] No new survivors introduced by remediation PRs.
- [ ] Gate 5 `mutation_testing.json` passes.

---

## PLAN-007 — R14 `api-task` Community Oversized (67 nodes)  *(HIGH)*

**Owner.** @arch-lead
**Target.** 2026-09-21
**Status.** OPEN

### Strategy
1. **Sub-community split.** Split `api/` into routers / deps / app factory / schemas sub-packages; expect each ≤ 50 nodes.
2. **Cohesion floor.** After split, community cohesion ≥ 0.3.
3. **Test isolation.** No regression in `tests/api/`.

### Acceptance Criteria
- [ ] `gate_evidence/gate5/architecture.json`: no community with `size > 50`.
- [ ] All existing API tests pass without modification.

---

## PLAN-008 — R4 403 洩漏資源存在性  *(MEDIUM @ 9)*

**Owner.** @api-lead
**Target.** 2026-09-07
**Status.** MONITORED

### Strategy
Authorize-before-fetch enforced in dependency layer (§8 #6); 401/403 share identical body.

### Acceptance Criteria
- [ ] Pen-test fails to enumerate resource IDs via differential response.
- [ ] 401/403 contract test asserts byte-equal body for unknown vs forbidden resources.

---

## PLAN-009 — R7 `CancelledError` Swallow  *(MEDIUM @ 9)*

**Owner.** @backend-lead
**Target.** 2026-08-31
**Status.** MONITORED

### Strategy
AST gate (`ast-error-handling`) blocks `except Exception: pass` patterns; tests cancel long-running task and assert exit ≤ 1s.

### Acceptance Criteria
- [ ] No bare `except Exception` containing `pass` or `return`.
- [ ] Cancellation-test suite green.

---

## PLAN-010 — R8 任務 Timeout 留下孤兒進程  *(MEDIUM @ 9)*

**Owner.** @backend-lead
**Target.** 2026-09-07
**Status.** MONITORED

### Strategy
`subprocess.run(..., timeout=N)` → on `TimeoutExpired`: `proc.kill()` + `await proc.wait()` (sync variant via `proc.wait()`).

### Acceptance Criteria
- [ ] Test asserts no remaining child process in same pgid after timeout.
- [ ] Mutation score of runner ≥ 85%.

---

## PLAN-011 — R10 連線池耗盡  *(MEDIUM @ 9)*

**Owner.** @backend-lead
**Target.** 2026-09-14
**Status.** OPEN

### Strategy
Pool size = worker count × per-worker headroom; `pool_pre_ping` on; semaphore wrapping engine for outer bound.

### Acceptance Criteria
- [ ] Load test (10k RPS for 5 min) shows no pool wait > 50ms p99.
- [ ] Auto-scaling threshold documented in runbook.

---

## PLAN-012 — R11 Transitive License 不相容  *(MEDIUM @ 9)*

**Owner.** @release-mgr
**Target.** 2026-09-07
**Status.** MONITORED

### Strategy
`scancode` + `pip-licenses` deny list (GPL/AGPL/SSPL); CI fails on hit; lock-file PR review.

### Acceptance Criteria
- [ ] `license_compliance.json` clean.
- [ ] Quarterly dependency audit signed off.

---

## PLAN-013 — R15 `versions-task` 低內聚 (0.1481)  *(MEDIUM @ 9)*

**Owner.** @arch-lead
**Target.** 2026-09-21
**Status.** OPEN

### Strategy
Decompose Alembic revisions into smaller, single-concern migrations; raise cohesion to ≥0.3.

### Acceptance Criteria
- [ ] `gate_evidence/gate5/architecture.json`: versions-task cohesion ≥ 0.3.
- [ ] Migration runtime ≤ 1.2× current.

---

## 4. Tracking Discipline

- **Weekly burn-down.** Every Monday update `RISK_STATUS_REPORT.md` `Status` field.
- **Escalation.** Any plan past 70 % of its window without progress → escalate to @release-mgr.
- **Evidence.** Each plan MUST close with at least one test/measurement artifact path under `05-verification/` or `gate_evidence/gate5/`.
- **Sign-off.** All HIGH/CRITICAL plans require dual approval: plan owner + @release-mgr.

---

## 5. Out-of-Scope (no plan needed)

| ID  | Reason                                |
|-----|---------------------------------------|
| R2  | Score 8 < 9; gate-monitored           |
| R12 | Score 6 < 9; gate-monitored           |
| R16 | Subset of R13; tracked under PLAN-006 |
