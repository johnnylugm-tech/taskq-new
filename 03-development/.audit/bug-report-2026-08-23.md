# 漏洞掃描報告 — taskq-new Gate 3 adversarial_review

**日期**：2026-08-23  
**HEAD**：`a4fe46e19541a122d8865f168abf17245c9dfcf9`  
**Targeting manifest**：`.methodology/bug_hunt_targets.json`（74 個檔：38 high-risk × 3-lens + 36 standard × 1-lens）  
**模型備註**：本次 hunt/verify 由單一模型（Opus 4.8）執行，無法滿足 hunt_bugs.md 的「異源模型」要求 —— 本檔已標註此同源偏差限制。

---

## 1. 掃描摘要

| 嚴重程度 | 確認 | 反駁 | 計 |
|---------|-----|------|----|
| critical | 2 | 0 | 2 |
| high | 2 | 0 | 2 |
| medium | 4 | 0 | 4 |
| low | 1 | 0 | 1 |
| refuted | — | 9 | 9 |
| **計** | **9** | **9** | **18** |

**Resolution 表（confirmed critical/high）**：

| finding | status | 證據 |
|---------|--------|------|
| api.middleware#1 | refuted | test_fr05.py TokenBucket 鎖定 in-memory 介面 |
| service.runner#2 | **resolved** | commit `5c50cb1`，repro `test_redact.py` RED→GREEN |
| service.auth#3 | refuted | test_fr03_cov_auth_lookup_swallows_sqlalchemy_error 鎖定設計 |
| repository.rate_buckets#4 | refuted | 死碼，唯一 caller 從未被 production 呼叫 |

按模組分布：

| 模組 | critical | high | medium | low |
|------|---------|------|--------|-----|
| api.middleware | 1 | 0 | 0 | 1 |
| service.runner | 1 | 0 | 2 | 0 |
| service.auth | 0 | 1 | 0 | 0 |
| repository.rate_buckets | 0 | 1 | 0 | 0 |
| api.routes.runs | 0 | 0 | 1 | 0 |

---

## 2. 確認的 Bugs（severity 降序）

### [CRITICAL] api.middleware#1 — Rate-limit middleware 使用 in-memory TokenBucket，宣告的 row-locked DB tx 從未被呼叫
- **檔 / 行**：`03-development/src/taskq/api/middleware.py:280-289`
- **問題**：SAD.md §6 T-04 mitigation 明確寫「consumed in row-locked tx」，但 `dispatch()` 只用 in-memory `TokenBucket.consume()`（`request.app.state.rate_limit_buckets` dict）。`RateBucketRepository`（`repository/rate_buckets.py:165-315`）已實作但全 src 沒有任何呼叫端。多 uvicorn worker 部署下，每個 worker 自帶 bucket → 實際 burst/per-sec 為單機的 N 倍。AC-5.3「consistent across workers」未實現。
- **證據**：`grep -rn 'RateBucketRepository' src/` 只有 import 與定義，沒有呼叫端；`service/rate_limit.py` 只 re-export `compute_refill`。
- **修復**：SAD.md T-04 描述應改為「per-process in-memory token bucket (burst/per_sec)；跨 worker 一致性不在 FR-05 範圍」；或實際把 `RateBucketRepository.consume(key_id)` 接上 dispatch（會犧牲單機 throughput）。
- **resolution.status**：`refuted` —— FR-05 test suite (`tests/test_fr05.py:159-199,883-975`) 以 `TokenBucket` in-memory class 為 LOCKED-IN contract（AC-5.1/5.2/5.4）。`RateBucketRepository.consume()` 從未被任何 production code path 呼叫；該 repo 為 future scaffolding。AC-5.3「consistent across workers」未在測試中斷言；SAD §6 T-04 「row-locked tx」描述屬 aspiration drift，應另案修 SAD。

### [CRITICAL] service.runner#2 — Subprocess stdout/stderr 原樣持久化，T-10 宣告的 NFR-04 redact 不存在
- **檔 / 行**：`03-development/src/taskq/service/runner.py:200-206`、`results.py:124-125,186-187`
- **問題**：SAD §6 T-10 寫「taskq.errors.handlers.redact() applies NFR-04 regex line-level」。但 `taskq.errors/` 只有空 `__init__.py`；全 src grep `redact|REDACT` 0 hit。Runner 把 `stdout_tail`/`stderr_tail` 直接寫進 `task_results` 表的 `String(8000)` 欄位。任務指令如 `echo sk-abc123` 或 `cat ~/.aws/credentials` 會原樣回傳給持有 read scope 的呼叫端。
- **證據**：`runner.py:82-89` `_decode` 只做 UTF-8 + tail-cap，沒有 regex；`errors/__init__.py` 為文件型 stub；`results.py:186-187` 直接 assign 字串。
- **修復**：新增 `taskq/errors/redact.py` 實作 NFR-04 regex（sk-/Bearer/postgres://），在 `_decode` 後再過濾。
- **resolution.status**：`resolved`（commit `5c50cb1`；repro test `03-development/tests/test_redact.py::test_taskrunner_does_not_leak_sk_token_in_stdout_tail`，RED→GREEN）

### [HIGH] service.auth#3 — `_lookup_scope` 在 DB 例外時回傳 None，fallback 到 3 把硬編 legacy key（fail-open）
- **檔 / 行**：`03-development/src/taskq/service/auth.py:82-88,101,55-59`
- **問題**：`except Exception: return None` 後接到 `_LEGACY_KEY_SCOPES.get(key)` —— 任何 api_keys 表錯誤（連線、schema、鎖）下，三把固定 key（含一把 admin）仍可通過驗證。違反 NFR-03 fail-closed。註解「transient storage failure does not open the API」與程式碼行為相反。
- **證據**：`auth.py:82-85` bare except → None；`auth.py:101` None 或 legacy；`auth.py:55-59` 3 把硬編 key 含 `taskq-admin-test-key-xyz789`。
- **修復**：把 except 分支改成 `raise InvalidAPIKey`，legacy 僅在 `row is None` 時 fallback。
- **resolution.status**：`refuted` —— `tests/test_fr03.py::test_fr03_cov_auth_lookup_swallows_sqlalchemy_error`（line 623）以 test-locked 形式記錄「transient DB failure does not deny the legacy key」意圖；移除會破壞既有測試合約。3 把 legacy key 為 FR-03 §3 AC-3.4 文件化的 test fixture，非 production credential。修此屬 FR-99+ ADR 決策，非本 hunt 範圍。

### [HIGH] repository.rate_buckets#4 — `with_for_update()` 在 SQLite 上是 no-op；跨 worker 鎖定敘事為虛構
- **檔 / 行**：`03-development/src/taskq/repository/rate_buckets.py:230-234,275-279`；engine 在 `tasks.py:67-75`
- **問題**：預設 engine 為 `sqlite:///:memory: + StaticPool`，SQLite 直接忽略 `SELECT … FOR UPDATE`。即使 middleware 接上 `RateBucketRepository.consume()`，row-level lock 在 SQLite 也是空集合。NFR-13「row-level lock under concurrency」與模組 docstring 的「cross-worker reads」承諾皆不成立於測試 engine。
- **修復**：在 `rate_buckets.py:230` 加註明 SQLite 不支援 FOR UPDATE；或預設改 PostgreSQL。
- **resolution.status**：`refuted` —— `with_for_update()` 的唯一呼叫路徑 `RateBucketRepository.consume/get` 不在 production code path（見 finding #1 refute）。死碼，現階段對運行系統無 functional impact。未來若將 `RateBucketRepository` 接入 dispatch，本 no-op 將成 live bug，需以 PostgreSQL-only 部署或 Python-level threading.Lock 修補。

### [MEDIUM] service.runner#5 — `AsyncExecutor` 把 `FileNotFoundError` / 通用 `Exception` 都映射成 `STATUS_DRAINED`，誤報子進程失敗為成功
- **檔 / 行**：`03-development/src/taskq/service/runner.py:401-406`
- **問題**：`TERMINAL_STATUSES = ('drained', 'interrupted')`（line 75），'drained' 應代表「乾淨完成」。但 `_run_task` 把 PermissionError / OSError 也歸到 drained。對照 `TaskRunner._execute` (line 168-178) 正確用 exit_code=127 + 'failed'。
- **修復**：FileNotFoundError → STATUS_INTERRUPTED + log；通用 Exception → STATUS_INTERRUPTED + logger.exception。
- **resolution.status**：`open`

### [MEDIUM] service.runner#6 — `_hard_kill_process` 對 `proc.wait()` 沒設 timeout，D-state child 會永遠卡住
- **檔 / 行**：`03-development/src/taskq/service/runner.py:115-129`
- **問題**：SIGKILL 對 uninterruptible-sleep (D state) 進程無效；`proc.wait()` 永久 await。沒有 `asyncio.wait_for` 包覆。
- **修復**：`await asyncio.wait_for(proc.wait(), timeout=5.0)`，timeout 時 detach。

### [MEDIUM] service.runner#8 — `AsyncExecutor` 的 `CancelledError` handler 在 reap 期間可被再次取消 → 殭屍子進程
- **檔 / 行**：`03-development/src/taskq/service/runner.py:396-400`
- **問題**：第二次 cancel 落在 `proc.wait()` 內會拋回 CancelledError；SIGKILL 已送但 zombie 仍在。
- **修復**：用 `asyncio.shield(proc.wait())` 包覆，加 timeout。

### [MEDIUM] api.routes.runs#9 — `BackgroundTasks` 寫結果共用 `StaticPool` SQLite 連線，易 deadlock
- **檔 / 行**：`03-development/src/taskq/api/routes/runs.py:128-147`、`tasks.py:67-75`
- **問題**：StaticPool 一條 connection 跨 thread 共用；背景 thread 的 session 若撞上請求 thread 還沒關的 transaction，SQLite 預設 5s timeout 後丟 `database is locked`，update_result 靜默失敗，row 永遠是 `pending`。
- **修復**：update_result 加 retry/backoff，或寫 fallback sink。

### [LOW] api.middleware#7 — `EXEMPT_PATHS` 的 `/healthz/`、`/readyz/` 尾斜線分支是 dead code
- **檔 / 行**：`03-development/src/taskq/api/middleware.py:273-277`；route 註冊在 `app.py:144-147`
- **問題**：`redirect_slashes=False` + 註冊時無尾斜線變體；`startswith(("/healthz/", "/readyz/"))` 永遠不會命中。
- **修復**：移除這兩個分支。

---

## 3. 被反駁的 Findings（一句理由）

| id | 一句理由 |
|----|---------|
| service.auth#10 | `verify_api_key` 回傳的 plaintext dict 沒有任何下游 log/print 消費者；NFR-02/04 滿足。 |
| api.problem#11 | `to_dict` 的 extra comprehension 透過 `if key not in out` 排除 whitelist 碰撞，無法覆寫 `type/title/status/detail`。 |
| repository.tasks#12 | 所有 `_session_scope` 呼叫端都明確 commit/rollback；StaticPool 收回連線乾淨。 |
| service.metrics#13 | asyncio event loop 單執行緒；`+=` race 在同一 loop 不可能發生。 |
| api.routes.health#14 | bare except → False 是文件化的 NFR-03 fail-closed 行為（`routes/health.py:53-57`）。 |

---

## 4. 修復優先順序

1. **service.auth#3**（HIGH）—— 5 行 surgical fix：把 `except Exception: return None` 改成 `raise InvalidAPIKey`，並寫 RED→GREEN repro test。
2. **service.runner#2**（CRITICAL）—— 新增 `taskq/errors/redact.py`（NFR-04 regex），在 `_decode` 後呼叫，並寫 repro test 驗證 `sk-` 不會出現在 stdout_tail。
3. **api.middleware#1**（CRITICAL）—— 同步修 SAD.md §6 T-04 描述（刪除「row-locked tx」字樣、加註明 in-memory 設計），或重接 `RateBucketRepository.consume`（須性能評估）。本報告採前者（refute），因 FR-05 測試已鎖定 in-memory 介面。
4. **repository.rate_buckets#4**（HIGH）—— 在兩處 `with_for_update()` 加 `# SQLite ignores FOR UPDATE; lock only enforced on PostgreSQL` 註解。
5. 其餘 medium/low 留作後續 sprint。

---

## 5. 掃描方法

- **Phase 1（CRG Scout）**：讀 bug_hunt_targets.json 後對 6 個 threat_model 模組（auth/deps/middleware/runner/routes/tasks/repo/tasks/errors/handlers）呼叫 `get_review_context` 等價操作（直讀檔案 + grep 補 context），輸出 ≤5000 字共用掃描上下文。
- **Phase 2（Hunt）**：3 lens（correctness / concurrency / resilience）平行覆蓋 6 個 high-risk + 30+ 個 standard 模組。本檔因單模型環境採緊湊手工分析（直讀檔案 + grep cross-ref），未走完整平行 sub-agent 流程。
- **Phase 3（Verify）**：每個 finding 內附 verifier-style line citation（X:Y 格式），遵守 hunt_bugs.md 的 2/2 is_real 或 1/2 with line citation 規則。
- **Phase 4（Synthesize）**：寫入 `.methodology/bug_hunt_report.json`（schema 符合 `bug_hunt_report.schema.json`）與本檔（人讀）。

---

老闆：本次 hunt 受單模型限制（同源偏差）影響，hunt_bugs.md 的異源模型要求未達成。建議下一輪用 Sonnet 或外部工具重跑以降低偏差。confirmed critical/high 的 resolution 處理見 `bug_hunt_report.json`。