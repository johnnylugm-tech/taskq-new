# RISK STATUS REPORT — taskq-new

**Project:** taskq-new
**Generated:** 2026-08-24
**Phase:** P7 — Per-FR Delta (Risk Author)
**Snapshot date:** 2026-08-24
**Next refresh:** 2026-08-31 (weekly cadence; see `RISK_MITIGATION_PLANS.md` §4)

**Companions.**
- Risk inventory: `07-risk/RISK_REGISTER.md`
- Mitigation actions: `07-risk/RISK_MITIGATION_PLANS.md`

---

## 1. Headline

- **16 risks tracked** (12 from `SPEC.md` §9 + 4 operational from Gate 3/4 evidence).
- **1 CRITICAL**, **6 HIGH**, **6 MEDIUM**, **3 LOW**.
- **3 OPEN** remediation items requiring action before next release:
  - **R13** mutation score 72.1% → must reach ≥80% (PLAN-006).
  - **R14** `api-task` community oversized 67 → split before next gate (PLAN-007).
  - **R11** license compliance already green but ongoing (PLAN-012).
- **Gate 4 PASSED** (score 94.6); risks above are forward-looking, not regressions.

---

## 2. Risk Roll-up

| Tier     | Count | % of total |
|----------|-------|-----------:|
| CRITICAL | 1     | 6.3 %      |
| HIGH     | 6     | 37.5 %     |
| MEDIUM   | 6     | 37.5 %     |
| LOW      | 3     | 18.8 %     |
| **Total**| **16**| **100 %**  |

Aggregate residual risk after current mitigations:

| Tier   | Residual |
|--------|----------|
| CRITICAL | 0 (R5 mitigation in progress; p95 within SLA today) |
| HIGH     | 4 (R13, R14 active; R1, R3, R6, R9 monitored) |
| MEDIUM   | 5 (R7, R8, R11 mitigated; R4, R10, R15 in plan) |
| LOW      | 3 (R2, R12, R16) |

---

## 3. Per-Risk Status Table

| ID  | Risk                          | Score | Tier     | Mitigation owner     | Target date  | Status   | Plan ID   |
|-----|-------------------------------|-------|----------|---------------------|--------------|----------|-----------|
| R1  | v3 資料搬遷遺失資料            | 12    | HIGH     | @migration-owner    | 2026-09-14   | OPEN     | PLAN-002  |
| R2  | SQL injection                  | 8     | MEDIUM   | @security-lead      | continuous   | MONITORED | (gate)    |
| R3  | API key 洩漏                   | 12    | HIGH     | @security-lead      | 2026-09-07   | MONITORED | PLAN-003  |
| R4  | 403 洩漏資源存在性             | 9     | MEDIUM   | @api-lead           | 2026-09-07   | MONITORED | PLAN-008  |
| R5  | N+1 查詢在大表上崩潰           | 16    | CRITICAL | @backend-lead       | 2026-09-07   | IN PROG. | PLAN-001  |
| R6  | 錯誤 body 洩漏內部結構         | 12    | HIGH     | @api-lead           | 2026-08-31   | MONITORED | PLAN-004  |
| R7  | `CancelledError` 被吞          | 9     | MEDIUM   | @backend-lead       | 2026-08-31   | MONITORED | PLAN-009  |
| R8  | 任務 timeout 孤兒進程          | 9     | MEDIUM   | @backend-lead       | 2026-09-07   | MONITORED | PLAN-010  |
| R9  | 部署後忘記跑 migration         | 12    | HIGH     | @sre                | 2026-08-31   | MONITORED | PLAN-005  |
| R10 | 連線池耗盡                     | 9     | MEDIUM   | @backend-lead       | 2026-09-14   | OPEN     | PLAN-011  |
| R11 | transitive license 不相容      | 9     | MEDIUM   | @release-mgr        | 2026-09-07   | MONITORED | PLAN-012  |
| R12 | rate bucket 競態               | 6     | LOW      | @backend-lead       | continuous   | MONITORED | (gate)    |
| R13 | Mutation score 72.1%           | 12    | HIGH     | @qa-lead            | 2026-09-21   | OPEN     | PLAN-006  |
| R14 | `api-task` community oversized | 12    | HIGH     | @arch-lead          | 2026-09-21   | OPEN     | PLAN-007  |
| R15 | `versions-task` low cohesion    | 9     | MEDIUM   | @arch-lead          | 2026-09-21   | OPEN     | PLAN-013  |
| R16 | 99 mutation survivors          | 6     | LOW      | @qa-lead            | 2026-09-21   | TRACKED  | PLAN-006 (subset) |

### Status legend
- **OPEN** — formal mitigation plan accepted; remediation not started or partial.
- **IN PROGRESS** — owner actively executing plan.
- **MONITORED** — gate enforces mitigation; no further action unless gate regresses.
- **TRACKED** — covered by another plan; reported here for completeness.
- **CLOSED** — would appear once plan acceptance criteria met; none yet at this snapshot.

---

## 4. Active Plans — Burn-down

| Plan ID  | Risk | Owner         | Target   | % complete (est.) | Blocker / note                                  |
|----------|------|---------------|----------|-------------------|-------------------------------------------------|
| PLAN-001 | R5   | @backend-lead  | 09-07    | 30 %              | Need ≥10k-row fixture; load-test rig provisioning|
| PLAN-002 | R1   | @migration-owner | 09-14 | 0 %              | Round-trip test not yet authored                |
| PLAN-003 | R3   | @security-lead | 09-07    | 60 %              | Argon2 migration pending; secrets scan green     |
| PLAN-004 | R6   | @api-lead      | 08-31    | 80 %              | Whitelist draft; awaiting security review       |
| PLAN-005 | R9   | @sre           | 08-31    | 70 %              | /readyz wiring complete; drill runbook pending   |
| PLAN-006 | R13  | @qa-lead       | 09-21    | 5 %               | Triage CSV not started                           |
| PLAN-007 | R14  | @arch-lead     | 09-21    | 0 %               | Awaiting sub-package split design                |
| PLAN-008 | R4   | @api-lead      | 09-07    | 50 %              | 401/403 contract test authored; pen-test queued  |
| PLAN-009 | R7   | @backend-lead  | 08-31    | 90 %              | AST gate active; cancellation test green         |
| PLAN-010 | R8   | @backend-lead  | 09-07    | 60 %              | kill/wait pattern adopted in runner; orphan test draft |
| PLAN-011 | R10  | @backend-lead  | 09-14    | 20 %              | Semaphore wrapper design                         |
| PLAN-012 | R11  | @release-mgr   | 09-07    | 80 %              | Deny list active; quarterly audit scheduled      |
| PLAN-013 | R15  | @arch-lead     | 09-21    | 0 %               | Decomposition strategy pending                   |

---

## 5. Top-3 Items Needing Attention

1. **R13 / PLAN-006 — Mutation 72.1%.** Slowest-moving open item and a Gate 5 blocker; assign one engineer full-time for 1 week.
2. **R14 / PLAN-007 — api-task community oversized.** Architecture risk feeding multiple test-failure fan-in paths; design split this sprint.
3. **R5 / PLAN-001 — N+1 at scale.** Highest score; load test infrastructure must land before target date.

---

## 6. Gate Cross-Walk

| Gate dimension                  | Linked risk(s)              | Status |
|---------------------------------|-----------------------------|--------|
| NFR-01 Performance              | R5, R10                     | PASS   |
| NFR-02 Security                 | R2, R3, R4, R6              | PASS   |
| NFR-03 Error handling           | R7                          | PASS   |
| NFR-04 Security (key)           | R3                          | PASS   |
| NFR-05 Documentation            | —                           | PASS   |
| NFR-06 Architecture constraints | R14, R15                    | PASS (with caveats) |
| NFR-07 License compliance       | R11                         | PASS   |
| NFR-08 Mutation testing         | R13, R16                    | PASS (72.1%) |
| NFR-09 Test assertion quality   | R13, R16                    | PASS   |
| NFR-10 Integration coverage     | R5, R8, R9                  | PASS   |
| NFR-11 Readability              | —                           | PASS   |
| NFR-12 Execute verification     | R1, R9                      | PASS   |

---

## 7. Decision Log (this period)

| Date       | Decision                                                              | Rationale                                    |
|------------|-----------------------------------------------------------------------|----------------------------------------------|
| 2026-08-24 | Adopt SPEC.md §9 numbering for risk IDs (R1–R12); not the dispatch seed | Dispatch seed described a different project  |
| 2026-08-24 | Add operational risks R13–R16 from Gate 4 evidence                   | Gate results surface real, measurable risks  |
| 2026-08-24 | Treat `deferred_fixes.md` / `issue_registry.json` as absent           | Files not present in repo; revisit if added  |

---

## 8. Forward-Looking Indicators

- Re-run mutmut after PLAN-006 PRs land; expect delta ≥ +5 pp/iteration.
- After PLAN-007 split, re-run `crg` graph build; expect ≤ 5 communities with `size > 50`.
- After PLAN-005 deploy drill, capture post-mortem even on success.

---

## 9. Sign-off

- **Risk Author (P7):** ____________________  Date: 2026-08-24
- **Release Manager:** ____________________  Date: __________
- **Project Lead:** ____________________  Date: __________
