# RISK REGISTER — taskq-new

**Project:** taskq-new
**Generated:** 2026-08-24
**Phase:** P7 — Per-FR Delta (Risk Author)
**Source(s):** `SPEC.md` §9 risk matrix (R1–R12), `.methodology/gate4_result.json`,
`.methodology/gate_evidence/gate4/architecture.json`, `.methodology/mutation_score.json`,
`.methodology/bug_hunt_report.json`, `.methodology/gate3_result.json`.

> **Scoring scale (1–5).**
> Likelihood: 1=Rare, 2=Unlikely, 3=Possible, 4=Likely, 5=Almost certain.
> Impact:    1=Negligible, 2=Minor, 3=Moderate, 4=Major, 5=Catastrophic.
> Tier: **CRITICAL ≥ 20** · **HIGH 12–19** · **MEDIUM 6–11** · **LOW ≤ 5**.
> Mitigation-plan threshold: **score ≥ 9 → formal plan in `RISK_MITIGATION_PLANS.md`**.

> **Note on seed list.** The P7 dispatch referenced R1=concurrent write / R2=subprocess
> hang / R3=breaker deadlock / R4=stale cache. Those IDs do not exist in this repo's
> `SPEC.md` §9. This register seeds from the **actual `SPEC.md` §9 (R1–R12)** and adds
> operational risks surfaced by Gate 3/4 evidence.

---

## 1. Executive Summary

| Tier        | Count | IDs                                                          |
|-------------|-------|--------------------------------------------------------------|
| CRITICAL    | 1     | R5                                                           |
| HIGH        | 6     | R1, R3, R6, R9, R13, R14                                     |
| MEDIUM      | 6     | R4, R7, R8, R10, R11, R15                                    |
| LOW         | 3     | R2, R12, R16                                                 |

**Total tracked risks: 16** (12 from SPEC.md §9 + 4 operational risks R13–R16).

---

## 2. SPEC.md §9 — Authoritative Risk Matrix (R1–R12)

Severity numbers below are derived from SPEC.md qualitative bands
(高/中/低 → 4/3/2 for Impact; 高/中/低 → 4/3/2 for Likelihood).

| ID  | Risk                              | Lik. | Imp. | Score | Tier     | Spec anchor                |
|-----|-----------------------------------|------|------|-------|----------|----------------------------|
| R1  | v3 資料搬遷遺失資料               | 3    | 4    | 12    | HIGH     | FR-07 / §8 #12             |
| R2  | SQL injection                     | 2    | 4    | 8     | MEDIUM   | NFR-02                     |
| R3  | API key 洩漏                      | 3    | 4    | 12    | HIGH     | FR-03                      |
| R4  | 403 洩漏資源存在性                | 3    | 3    | 9     | MEDIUM   | FR-04 / §8 #6              |
| R5  | N+1 查詢在大表上崩潰              | 4    | 4    | 16    | CRITICAL | NFR-01 / §8 #14            |
| R6  | 錯誤 body 洩漏內部結構            | 4    | 3    | 12    | HIGH     | FR-10                      |
| R7  | `CancelledError` 被吞 → 關閉卡死  | 3    | 3    | 9     | MEDIUM   | NFR-03                     |
| R8  | 任務 timeout 留下孤兒進程         | 3    | 3    | 9     | MEDIUM   | FR-08 / §8 #25             |
| R9  | 部署後忘記跑 migration            | 3    | 4    | 12    | HIGH     | FR-09 / §8 #11             |
| R10 | 連線池耗盡                        | 3    | 3    | 9     | MEDIUM   | FR-06/08                   |
| R11 | transitive license 不相容         | 3    | 3    | 9     | MEDIUM   | NFR-07                     |
| R12 | rate bucket 競態超放行            | 3    | 2    | 6     | LOW      | FR-05                      |

---

## 3. Operational Risks (Gate 3/4 evidence-derived)

| ID  | Risk                                  | Lik. | Imp. | Score | Tier   | Evidence                                              |
|-----|---------------------------------------|------|------|-------|--------|-------------------------------------------------------|
| R13 | Mutation score 72.1% 偏離 ≥80% 標線    | 4    | 3    | 12    | HIGH   | `.methodology/mutation_score.json`                   |
| R14 | `api-task` community oversized (67)   | 3    | 4    | 12    | HIGH   | gate4/architecture.json: `oversized(67)`             |
| R15 | `versions-task` cohesion 0.1481 < 0.15 | 3    | 3    | 9     | MEDIUM | gate4/architecture.json: `low_cohesion(0.15)`        |
| R16 | 99 mutation survivors 未根治          | 3    | 2    | 6     | LOW    | `mutation_score.json`: `bad_survived=99`             |

---

## 4. Detailed Risk Cards

### R1 — v3 資料搬遷遺失資料  *(HIGH, score 12)*
- **Category:** Data integrity / Migration.
- **Description:** v3 schema 拆 `result_json` 至 `task_results` 表；遷移腳本 bug 可能造成欄位值丟失、型別收斂錯誤、或 up/down 不對稱。
- **Triggers:** 大表 (≥1M rows)、partial index 重建、欄位 NOT NULL 約束落差、transactional DDL 失敗後部分 commit。
- **Mitigation approach:**
  - 往返可逆性測試以真實 DB 逐欄比對 (§8 #12)。
  - baseline test 在 PR 前必跑；down-migration 後再 up 必須 byte-equal。
  - 預載樣本：≥ 100k 隨機列 + 邊界值 (NULL/empty/超長/Unicode)。
- **Status:** mitigated by tests (FR-07 §8 #12). Re-verify per release.

### R2 — SQL injection  *(MEDIUM, score 8)*
- **Category:** Security.
- **Description:** 任何 SQL 字串拼接或 f-string interpolation 可被利用注入。
- **Mitigation approach:**
  - 全面禁用字串拼接 (`grep` gate 阻擋)。
  - SQLAlchemy ORM / 參數化綁定為唯一路徑。
  - CI: `.methodology/gate_evidence/gate4/security.json` 已驗證。
- **Status:** mitigated (NFR-02 contract enforced).

### R3 — API key 洩漏  *(HIGH, score 12)*
- **Category:** Security / Secrets.
- **Description:** API key 儲存、明文 log、或在錯誤回應中洩漏。
- **Mitigation approach:**
  - 雜湊儲存 (`hashlib.scrypt` 或 argon2)、常數時間比對 (`hmac.compare_digest`)。
  - 建立時明文只印一次，後續永遠以 hash 形式處理。
  - secrets_scanning gate (gate4) 必須 0 hits。
- **Status:** mitigated by FR-03 + gate.

### R4 — 403 洩漏資源存在性  *(MEDIUM, score 9)*
- **Category:** Security / Information disclosure.
- **Description:** 認證失敗 (401) 與授權失敗 (403) 回應差異可能讓攻擊者枚舉資源 ID。
- **Mitigation approach:**
  - 授權判定在資源查詢之前 (§8 #6)。
  - 401/403 統一 body shape, 不含 resource id / owner。
- **Status:** mitigated (FR-04).

### R5 — N+1 查詢在大表上崩潰  *(CRITICAL, score 16)*
- **Category:** Performance / Scalability.
- **Description:** ORM lazy loading 在列表 API 上對每列額外查詢;大表情境下 p95 突破 SLA 且連線池耗盡。
- **Mitigation approach:**
  - 顯式預載 (`selectinload`/`joinedload`)。
  - SQL 計數斷言:列表回應的 query count 必須 ≤ 上限 (§8 #14)。
  - pytest-benchmark 對大表樣本 (≥10k 列) 量測 p95。
  - 連線池上限 + `pool_pre_ping` 防止 stale conn (FR-06/08)。
- **Status:** partially mitigated — NFR-01 perf gate 通過; 仍需大規模資料驗證。

### R6 — 錯誤 body 洩漏內部結構  *(HIGH, score 12)*
- **Category:** Security / Information disclosure.
- **Description:** 未預期的例外或 framework default error 將 stack trace、SQL、模組路徑寫入 response。
- **Mitigation approach:**
  - RFC 7807 固定欄位 (`type`/`title`/`status`/`detail`/`instance`)。
  - `detail` 白名單;生產環境關閉 debug payload。
  - FastAPI exception handler 全域攔截 → Problem Details。
- **Status:** mitigated by FR-10.

### R7 — `CancelledError` 被吞 → 關閉卡死  *(MEDIUM, score 9)*
- **Category:** Error handling / Resource lifecycle.
- **Description:** `except Exception` 或 `except BaseException` 把 `asyncio.CancelledError` 吞掉，造成 shutdown 永久 hang。
- **Mitigation approach:**
  - 文字禁令 (`except Exception` 不得包含 `pass`/`return`)。
  - AST 掃描器 (`ast-error-handling` gate) 自動阻擋。
  - 測試斷言:取消任務必須在 ≤ 1s 內退出。
- **Status:** mitigated by NFR-03 + gate.

### R8 — 任務 timeout 留下孤兒進程  *(MEDIUM, score 9)*
- **Category:** Lifecycle / Resource leak.
- **Description:** subprocess `timeout=` 觸發但未 `kill()` 殘留子進程 → file handle / port / DB lock 殘留。
- **Mitigation approach:**
  - `subprocess.run(..., timeout=N)` → except 後 `proc.kill()` → `await proc.wait()`。
  - 測試斷言:觸發 timeout 後 ps 不得殘留同 group 的 child。
- **Status:** mitigated by FR-08 / §8 #25.

### R9 — 部署後忘記跑 migration  *(HIGH, score 12)*
- **Category:** Operations / Availability.
- **Description:** 新版本啟動後未跑 `alembic upgrade head`,DB schema 與 ORM 不一致 → runtime error。
- **Mitigation approach:**
  - `/readyz` fail closed (未到目標 revision → 503)。
  - 啟動 hook 內 `alembic upgrade head` 或 strict check。
- **Status:** mitigated by FR-09 / §8 #11.

### R10 — 連線池耗盡  *(MEDIUM, score 9)*
- **Category:** Performance / Resource exhaustion.
- **Description:** 高併發下連線數超過 DB 上限或 worker leak,後續 request 卡在 pool 等待。
- **Mitigation approach:**
  - `pool_pre_ping` 偵測 stale conn。
  - 併發上限 (semaphore) + pool size 與 worker 數匹配。
- **Status:** mitigated by FR-06/08.

### R11 — transitive license 不相容  *(MEDIUM, score 9)*
- **Category:** Legal / Compliance.
- **Description:** 間接依賴引入 GPL/AGPL/SSPL 等與專案 license 衝突的元件。
- **Mitigation approach:**
  - `requirements.txt` lock 檔 + 全樹掃描 (scancode / pip-licenses)。
  - CI 失敗 if deny list 出現。
- **Status:** mitigated by NFR-07 + gate.

### R12 — rate bucket 競態超放行  *(LOW, score 6)*
- **Category:** Concurrency / Security.
- **Description:** 多 worker 併發讀寫 rate-limit bucket 時因 read-modify-write 不原子而超放行。
- **Mitigation approach:**
  - 單一交易 + row-level lock (`SELECT ... FOR UPDATE`) on bucket row。
  - 整合測試: 1000 req/10s 情境下實放行量 ≤ 設定值。
- **Status:** mitigated by FR-05.

### R13 — Mutation score 72.1% 偏離 ≥80% 標線  *(HIGH, score 12)*
- **Category:** Test quality / Mutation testing (NFR-08).
- **Description:** `.methodology/mutation_score.json` 顯示 killed=256 / survived=99 / 685 untested;整體 72.1% 距 ≥80% 標線差 7.9 個百分點。99 個存活突變代表測試 assertion 弱、漏邊界。
- **Triggers:** 對 survivor 函式補 assertion、新增 boundary test、或刪除 dead code。
- **Mitigation approach:**
  - 排序 survivors by file → 逐檔補 `pytest.raises` / `==` / `>=` 等強斷言。
  - 移除重複/未使用的 production code 縮減突變集合。
  - 重跑 mutmut 直至 ≥80%。
- **Status:** open — action item `MUT-001` (see Mitigation Plans).

### R14 — `api-task` community oversized (67 nodes)  *(HIGH, score 12)*
- **Category:** Architecture / Cohesion (NFR-06).
- **Description:** `gate_evidence/gate4/architecture.json` 報告 `api-task` community 大小 67,超過 `community_oversized=50` 閾值。fan-in 集中 → 任何 regression 影響多個 test。
- **Mitigation approach:**
  - 拆分 `api/deps.py` / `api/app.py` / routers 子模組。
  - 重新量測:community size ≤ 50、cohesion ≥ 0.3。
- **Status:** open — action item `ARC-001`.

### R15 — `versions-task` cohesion 0.1481 < 0.15  *(MEDIUM, score 9)*
- **Category:** Architecture / Cohesion (NFR-06).
- **Description:** Alembic migrations `versions-task` community cohesion 0.1481,低於 `_cohesion_threshold=0.15`。
- **Mitigation approach:**
  - migrate scripts 拆細,每個 revision 對應單一 community 內聚群。
  - 或在 config 加 exclude file (謹慎評估)。
- **Status:** open — action item `ARC-002`.

### R16 — 99 mutation survivors 未根治  *(LOW, score 6)*
- **Category:** Test quality.
- **Description:** 99 個存活突變,雖分數勉強過 Gate,但代表 test assertion 弱於 R13 子集。
- **Mitigation approach:** 隨 R13 行動一併清理。
- **Status:** tracked under R13.

---

## 5. Cross-Reference

| Source                          | Items pulled in                                                |
|---------------------------------|----------------------------------------------------------------|
| `SPEC.md` §9                    | R1–R12                                                         |
| Gate 3 (`gate3_result.json`)    | (no `deferred_fixes.md` found; gate3 PASS, no carry-over)      |
| Gate 4 (`gate4_result.json`)    | R13 (mutation 72.1%), R14 (oversized), R15 (low cohesion)      |
| `.methodology/deferred_fixes.md`| **NOT FOUND** — no deferred items to import                   |
| `.sessi-work/issue_registry.json`| **NOT FOUND** — no registry to import                         |
| `bug_hunt_report.json`          | 0 CONFIRMED, status open/refuted/resolved — no new risk        |

> If deferred_fixes / issue_registry are introduced later, this register will be
> re-seeded in the next P7 delta run.

---

## 6. Risk Heatmap (text)

```
Impact →     1     2     3     4     5
Likelihood
   5 |       -     -     -     -     -
   4 |       -     -    R13   R5    -
   3 |       -    R12  R4,R7,  R1,R3, -
                       R8,R10,  R6,R9,
                       R11,R15  R14
   2 |       -     -     -     R2    -
   1 |       -     -     -     -     -
```

---

## 7. Change Log

| Date       | Change                                           | Author       |
|------------|--------------------------------------------------|--------------|
| 2026-08-24 | Initial register; 16 risks (R1–R16).             | P7 Risk Author |
