# RELEASE_CHECKLIST

## Pre-Release Checks
- [ ] All P1-P7 phases completed and artifacts generated.
- [ ] CI pipeline fully passed.
- [ ] Final Sign Off approved.
- [ ] Production environment provisioned.
- [ ] Rollback plan documented.

---

## Human Context (P8 append)

> Appended by P8 config reviewer (orch-post). Framework-generated pre-release
> checks above are kept verbatim from the P7→P8 advance-phase baseline.
> Companion evidence (Gate 4 PASS proof, quality_manifest composite_score, FR
> coverage, git tag/hash) lives in `FINAL_SIGN_OFF.md`, `RELEASE_NOTES.md`, and
> `.methodology/quality_manifest.json` — not duplicated here.

### Deployment runbook URL
- Primary: `https://runbooks.internal/taskq-api/release` (checklist, promote
  steps, smoke checks; same content as `08-config/RELEASE_CHECKLIST.md`).
- Companion: `08-config/RUNBOOK_DR.md` (disaster recovery — used only when the
  primary rollback in §7 of `CONFIG_RECORDS.md` does not restore service).
- Driver: internal CI job `taskq-api/prod-promote` (input: tag; output: deploy
  manifest + audit-log row). Invoked by SRE on-call, not by the release author.

### Rollback owner + on-call
- **Primary rollback owner**: P6 Release Author (signed off in
  `FINAL_SIGN_OFF.md` §1). Owns the decision to invoke the §7 SOP in
  `CONFIG_RECORDS.md`.
- **Secondary on-call**: SRE on-call (PagerDuty service `taskq-api-prod`,
  escalation policy `taskq-prod-escalation`). Takes over when the primary is
  unreachable > 10 min after page, or when the rollback is incident-driven
  rather than scheduled.
- **Tertiary**: Service-layer owner (named in `CONFIG_RECORDS.md` §8a). Engages
  for root-cause analysis; not expected to drive the rollback itself.
- Paging order: PagerDuty → `#inc-taskq` (Slack) → phone tree per escalation
  policy.

### Post-release monitoring dashboard
- Primary: Datadog dashboard `taskq-api / Release Health` (panels: p50/p95/p99
  latency on `/v1/tasks/*`, FR-02 task success rate, FR-04 429 rate, NFR-01
  throughput, `TASKQ_DRAIN_TIMEOUT` violations, FR-03 401/403 split).
- SLO board: `taskq-api / SLOs` (burn-rate alerts on availability 99.9% and
  latency p95 < 250 ms).
- Source-of-truth metrics for Gate 4 reproducibility: `.methodology/quality_manifest.json`
  → `gate_results.gate4.overall_score` and the per-dimension rows; re-runnable
  via `harness verify-gate --gate 4`.
- Watch window: first 60 minutes post-deploy are staffed (release author +
  SRE on-call); passive monitoring thereafter with 24h on-call escalation.

### Customer comms template
```
Subject: [taskq-api] v1.0.0 released — no action required

Hi <CUSTOMER_NAME>,

We have released taskq-api v1.0.0 (<RELEASE_DATE>). This is the first formal
release of the task-queue API.

What changed:
- 11 functional requirements are now GA (FR-01 .. FR-10, FR-99).
- API key auth (FR-03) + scope authorization (FR-04) are enforced on every
  /v1/* call — clients without a valid key now receive 401.
- Per-task execution timeout (FR-02) defaults to 30 s and is configurable
  via TASKQ_TASK_TIMEOUT.

What you need to do:
- Rotate any pre-release API keys you were issued for staging before
  <KEY_ROTATION_DEADLINE>. New keys are issued via `taskq key-create`.
- If you hit a 429 (rate-limited), your bucket is per-key; tune
  TASKQ_RATE_BURST / TASKQ_RATE_PER_SEC on your side.

Status & support:
- Status page: https://status.internal/taskq-api
- Runbook: https://runbooks.internal/taskq-api/release
- Incident channel (during incidents only): #inc-taskq

— <RELEASE_AUTHOR>, on behalf of the taskq-api release team
```
- Distribution: SendGrid template `taskq-api-release-announce`, audience =
  `customers.taskq-api` segment. Sent once per release, within 24 h of promote.
- Incident comms use a separate template (`taskq-api-incident`) and are NOT
  owned by the release author.
