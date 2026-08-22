---
key: 2026-08-23-fr99-finalization
source: gate-block
phase: 3
dimension: dispatch-loop
fr_ids: FR-99
created_at: 2026-08-23
---

**Failure:** FR-99 Gate 1 dispatch-loop non-convergent. .methodology/quality_manifest.json shows `gate_results.gate1.FR-99 = {score: 100.0, quality_complete: false}` (25 GATE_BLOCK decision logs with score=100, 6 fr-step-no-progress degradations, no `feat(FR-99): Gate1 PASS` commit). The canonical gate evaluator (all four dims at 100; tests 2/2; coverage 100% on framework_paths.py) evaluates to PASS — but the orchestration loop is permanently blocked.

**Root cause (Round 2026-08-23 verification):** NOT a verifier bug. NOT a coverage-classification proxy bug. **finalize-gate was never called for FR-99.** Sentinel `.sessi-work/sentinels/g1_p3_fr99.flag` exists but `.finalized` does not; git reflog shows no `feat(FR-99): Gate1 PASS` attempt; qc=false is the default value, not a `_mark_gate_commit_failed` rollback. The dispatch loop's `--step GATE1` invokes run-gate (re-evaluation) and never converges to a state where finalize-gate would be called. `_abort_no_progress_with_self_doubt` printed a stderr SELF-CHECK hint but no harness-routeable `[HARNESS-BUG]` banner, so the in-loop regex (`run-all.js:2342 et seq.`) never routed the failure to `harness-repair.js`.

**Why this lesson matters:** the original analysis (Fix 1 = verifier reads `.sessi-work/gate1_result.json` as second source; Fix 2 = snapshot adds `--cov`) was **refuted by verification**:
- Fix 1 is DANGEROUS — would defeat `_mark_gate_commit_failed`'s durability contract.
- Fix 2 is REDUNDANT — `_classify_snapshot_failure` doesn't consume coverage numbers; SSOT `validate_fr_coverage_immediate` already runs in COVERAGE-FIX inline fallback.

**Correct fix (this commit):**
- `_detect_evaluator_passed_but_commit_uncommitted` (new helper, `harness/cli/fr_cmds.py`): reads ephemeral `gate1_result.json` AND durable manifest, AND-conjunction (`rj.qc=true && rj.score>=100 && mfst.qc=false && mfst.score>=100`). The verifier (`verify_gate1_qc.py`) remains on manifest-only; this helper uses the ephemeral side only as a "diagnostic trigger" for the banner, not as a PASS condition.
- `_abort_no_progress_with_self_doubt` emits `[HARNESS-BUG] This is a bug in harness-methodology itself` banner to STDOUT when the helper returns truthy, with the exact `finalize-gate` recovery command. Routes through `routes_to_harness_repair` (`harness/core/fault_owner.py:317-326`).
- `_finalize_gate_preflight` honors `args.force` (was vestigial in `gate_cmds.py:2913`) by skipping the run-gate sentinel check. `--force` does NOT bypass tool-availability (S0a) — that's too dangerous.
- `_mark_gate_commit_failed` is NOT overridden by `--force` — preserving qc=true on commit failure would defeat the durability contract the manifest encodes.

**Operative recovery for FR-99 specifically:**
```bash
python harness_cli.py finalize-gate --gate 1 --phase 3 \
  --fr-id FR-99 --project /Users/johnny/projects/taskq-new
```
(`--force` not required — sentinel exists. `--force` only needed in future scenarios where the sentinel is gone.)

**Cross-references:**
- `.methodology/decision_logs/2026-08-22/HARNESS_FIX_BANNER_EMIT.yaml`
- `.methodology/decision_logs/2026-08-22/HARNESS_FIX_FORCE_FINALIZE.yaml`
- `.methodology/decision_logs/2026-08-22/HARNESS_HR17_WAIVER.yaml`
- HARNESS-BUG class lessons (canonical): `8f92e5f3cd92` (tool_score_fabrication), `89991b99680a` (tool_evidence_missing), `402c98763ee8` (arch_constraint_coverage), `ff1a95f6fd5c` (arch_constraint_unconfigured), `568f8dd375dd` (arch_contract_coverage).
