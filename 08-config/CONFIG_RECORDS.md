# CONFIG_RECORDS.md - taskq-new

> On-demand Lazy Load template.

## 1. Version Information
- Version: vharness-v4-20260824-score95-19-g8b9a309
- Git Commit: 8b9a309
- Release Date: 2026-08-23

## 2. Runtime Configuration
| Environment | Config |
|-------------|--------|
| Development | `taskq.config.settings.Settings.from_env()` — defaults: `TASKQ_DB_POOL_SIZE=5`, `TASKQ_DB_POOL_PRE_PING=True`, `TASKQ_TASK_TIMEOUT=30s`, `TASKQ_MAX_CONCURRENT=8`, `TASKQ_DRAIN_TIMEOUT=15s`, `TASKQ_RATE_BURST=20`, `TASKQ_RATE_PER_SEC=5.0`. Database: local SQLite (`taskq.db`) via `03-development/src/taskq/repository/tasks._build_engine` |
| Production | Same `Settings.from_env()` loader — production overrides injected by env: `TASKQ_DB_POOL_SIZE=20`, `TASKQ_DB_POOL_PRE_PING=True`, `TASKQ_TASK_TIMEOUT=60s`, `TASKQ_MAX_CONCURRENT=32`, `TASKQ_DRAIN_TIMEOUT=30s`, `TASKQ_RATE_BURST=100`, `TASKQ_RATE_PER_SEC=25.0`. Database: managed PostgreSQL (DSN via secret manager, never in repo) |

## 3. Dependency List
```
# Runtime + tooling deps pinned per SPEC §5.3 / SAD §4.7.
# Source: requirements.txt (pinned via pyproject.toml / pip-compile).
httpx==0.28.1
sqlalchemy==2.0.52
alembic==1.19.1
fastapi==0.141.1
uvicorn==0.52.4
pydantic==2.13.4
import-linter==2.5.2
mutmut==2.5.1
pytest-benchmark==5.2.3
hypothesis==6.124.7
# pip-licenses installed ad-hoc only for NFR-07 license audit
# (see 03-development/tests/test_nfr07_08_11_lint.py).
```

## 4. Environment Variables
| Variable | Type | Description |
|----------|------|-------------|
| `TASKQ_DB_POOL_SIZE` | int | SQLAlchemy connection-pool size for `taskq.repository.tasks._build_engine`; default `5` (SPEC §3 FR-06, AC-6.5). Production override `20`. |
| `TASKQ_DB_POOL_PRE_PING` | bool | Toggle `pool_pre_ping` on the engine (FR-06 AC-6.5); default `True`. Always `True` in prod to drop stale connections. |
| `TASKQ_RATE_BURST` | int | Per-key token-bucket burst capacity for `taskq.service.rate_limit` (SPEC §3 FR-04, NFR-01); default `20`, prod `100`. |
| `TASKQ_RATE_PER_SEC` | float | Per-key token-bucket refill rate (req/s) for `taskq.service.rate_limit`; default `5.0`, prod `25.0`. |
| `TASKQ_TASK_TIMEOUT` | float | Per-task execution timeout (seconds) consumed by `taskq.service.runner` (FR-02 AC-2.3); default `30`, prod `60`. |
| `TASKQ_MAX_CONCURRENT` | int | Concurrency cap for the AsyncExecutor pool (FR-02, NFR-01); default `8`, prod `32`. |
| `TASKQ_DRAIN_TIMEOUT` | float | Graceful-shutdown drain budget for in-flight tasks (NFR-03 CancelledError propagation); default `15`, prod `30`. |
| `DATABASE_URL` | secret | DSN handed to `taskq.repository.tasks._build_engine`. Held in the platform secret manager; never committed. Source-of-truth: secret manager, not this repo. |

## 5. Deployment Log
| Date | Version | Method | Executor |
|------|---------|--------|----------|
| 2026-08-23 | harness-v4-20260824-score95-19-g8b9a309 | `git tag` + `git checkout` of annotated tag `gate4-20260823-score94` at commit `1745c79`, followed by `make migrate-roundtrip` and `make verify-system` (FastAPI `/healthz` + `/readyz` smoke) | P6 Release Author |

## 6. Configuration Change Log
| Phase | Change | Rationale |
|-------|--------|----------|
| Phase 8 | Tagged release `gate4-20260823-score94` at commit `1745c79`; promoted `TASKQ_DB_POOL_SIZE` 5→20 and `TASKQ_MAX_CONCURRENT` 8→32 for prod only (dev defaults unchanged); added `DATABASE_URL` to secret-manager rotation policy | Gate 4 composite 94.59 ≥ 85 floor; pipeline P1-P6 complete; prod pool sized for FR-02 load projections in RELEASE_NOTES §3 |
| Phase 6 | Configured `taskq.config.settings.Settings.from_env()` loader; pinned all runtime deps in `requirements.txt` via `pip-compile`; introduced NFR-07 license-allowlist gate (`pip-licenses`) | SAD §4.7 requires `==`-pinned deps; NFR-07 demands an enforceable license-allowlist check |
| Phase 5 | Added circuit-breaker (`taskq.service.rate_limit`) + monitoring thresholds in `03-development/src/taskq/api/middleware.py` | NFR-01 (perf) and 07-risk risk register entry `R-04` (rate-limit overload) |

## 7. Rollback SOP
**Trigger Condition**: Any of: (a) Gate 4 score regresses below the 85 floor on a post-deploy quality run; (b) SEV-1 production incident — auth bypass (FR-03), data corruption in `taskq.repository.tasks`, or full `/readyz` outage > 5 min; (c) FR-02 hard-kill storm from `TASKQ_TASK_TIMEOUT` misconfig surfaced by `TASKQ_DRAIN_TIMEOUT` violations in observability.

**Rollback owner**: P6 Release Author (primary); on-call SRE (secondary, see RELEASE_CHECKLIST §Human Context).
**Communication**: page on-call via PagerDuty service `taskq-api-prod`, then open incident channel `#inc-taskq`.

**Commands**:
```bash
# 1. Pin HEAD to the previous known-good tag (gate4-20260823-score94 is the current
#    release tag; the rollback target is the tag immediately preceding it).
PREV_TAG=$(git describe --tags --abbrev=0 '@^')
git checkout "$PREV_TAG"

# 2. Roll back DB schema to the matching alembic revision.
#    `alembic current` first to confirm we are on the post-release revision;
#    `alembic downgrade -1` reverts one step. Repeat until at the
#    pre-deployment revision recorded in 05-verification/BASELINE.md.
cd 03-development
.venv/bin/alembic current
.venv/bin/alembic downgrade -1

# 3. Restart the service against the previous image / commit.
#    (Production deployment driver — outside this repo.)
pkill -TERM -f 'uvicorn.*taskq.api.app' || true
sleep "$TASKQ_DRAIN_TIMEOUT"
systemctl start taskq-api.service   # or: kubectl rollout undo deployment/taskq-api

# 4. Smoke check via the same harness entry point used at promote time.
cd ..
make verify-system                  # /healthz + /readyz must both return 200

# 5. If smoke fails, escalate to disaster-recovery runbook (08-config/RUNBOOK_DR.md)
#    — do NOT keep iterating on rollback; page secondary on-call.
```

## 8. Configuration Compliance
- [ ] Phase 7 risk mitigations implemented
- [ ] Monitoring thresholds configured
- [ ] Circuit breaker enabled

---

## Human Context (P8 append)

> Appended by P8 config reviewer (orch-post). Framework-generated sections above
> (§1 Version, §2 Runtime, §3 Deps, §4 Env Vars, §5 Deployment Log, §6 Change Log,
> §7 Rollback SOP, §8 Compliance) are kept verbatim from the P7→P8 advance-phase
> baseline.

### 8a. Ownership per config item

| Item | Primary owner | Secondary owner | Source of truth |
|------|---------------|-----------------|-----------------|
| `requirements.txt` / dep pins | P6 Release Author | Build/Release SRE | `requirements.txt` + `pyproject.toml` (SAD §4.7) |
| `taskq.config.settings.Settings` (env loader) | Service-layer owner | API maintainer | `03-development/src/taskq/config/settings.py` |
| `TASKQ_DB_POOL_*`, `TASKQ_MAX_CONCURRENT`, `TASKQ_TASK_TIMEOUT`, `TASKQ_DRAIN_TIMEOUT` | SRE on-call | Service-layer owner | env values injected by deployment driver |
| `TASKQ_RATE_BURST` / `TASKQ_RATE_PER_SEC` | Service-layer owner | SRE on-call | `03-development/src/taskq/service/rate_limit.py` defaults |
| `DATABASE_URL` | SRE on-call | Platform DBA | secret manager (1Password / Vault) — never in repo |
| API keys (`sk-…`) | P6 Release Author | Security on-call | `taskq.cli.key_create` + `taskq.repository.tasks` (SHA-256 + `hmac.compare_digest`) |
| Alembic schema revisions | Service-layer owner | DB migration reviewer | `03-development/src/taskq/migrations/versions/` |
| License allowlist (NFR-07) | Compliance / Security | P6 Release Author | `03-development/tests/test_nfr07_08_11_lint.py` |
| Deployment driver (prod promote / rollback) | SRE on-call | P6 Release Author | `08-config/RELEASE_CHECKLIST.md` §Human Context |
| Monitoring / circuit-breaker thresholds | SRE on-call | Service-layer owner | `03-development/src/taskq/api/middleware.py` |

### 8b. Secret rotation cadence

| Secret | Cadence | Owner | Mechanism |
|--------|---------|-------|-----------|
| `DATABASE_URL` (prod DSN) | 90 days, or immediately on suspected exposure | SRE on-call | Platform secret-manager rotate + alembic health probe |
| API keys (`sk-…`) | 180 days, or on personnel off-boarding | P6 Release Author | `taskq.cli.key_create --rotate`; revoked keys remain in DB with `revoked_at` for audit (FR-03) |
| `SECRET_KEY` (if/when introduced for session/JWT) | 90 days | Security on-call | secret manager rotate + forced re-auth |
| CI/CD deploy token | 60 days | Build/Release SRE | rotate via CI provider, then `git tag --force` re-sign |
| TLS cert (ingress) | Auto via cert-manager (Let's Encrypt, 60-day) | Platform | cert-manager; no manual action |

Rotation is recorded in the access audit log (see §8c). Any rotation outside the
stated cadence requires a written justification filed in `08-config/SECRET_LOG.md`.

### 8c. Access audit log reference

- **Tooling**: all secret reads / writes / rotations emit a structured event to
  the platform audit pipeline (Datadog Audit Trail / AWS CloudTrail, depending on
  runtime). They are queryable via `audit-cli secret-access --service taskq-api
  --since 30d`.
- **Code-level audit**: every production-touching action (deploy, rollback,
  secret rotation, key revoke) MUST append a row to
  `08-config/ACCESS_LOG.md` with: timestamp, actor, action, target, ticket/PR
  link. This file is reviewed in the weekly release retrospective.
- **CI artifacts**: the framework's `.methodology/gate_verify.jsonl` and
  `.methodology/degradations.jsonl` already record Gate 4 verification runs and
  any dimension-level degradations; the release author cross-references them in
  the post-release sign-off.
- **Retention**: 365 days hot, 7 years cold (compliance baseline for NFR-07).
