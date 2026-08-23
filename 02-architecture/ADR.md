# Architecture Decision Records

## Architecture Amendment — `taskq.models.rate_bucket` retargeted to `taskq.repository.rate_buckets`

- **When**: 2026-08-23T01:44:55.230467+00:00
- **Amended**: layer 'models'
- **Reason**: RateBucket ORM class co-located with its repository (taskq.repository.rate_buckets); P2 plan called for a separate models module that was never built and is not required by NFR-06 layer contract.
- **Recorded by**: `harness_cli.py amend-sab --resolve-phantom` (Gate 1 Architecture Amendment Protocol)

## Architecture Amendment — `taskq.api.routes.keys` dropped

- **When**: 2026-08-23T01:44:57.934359+00:00
- **Amended**: layer 'api'
- **Reason**: FR-03 key creation is served exclusively via taskq.cli.key_create; no admin HTTP keys router exists in the codebase, so the planned per-resource route file is not implementable within the FR-03/FR-04 scope.
- **Recorded by**: `harness_cli.py amend-sab --resolve-phantom` (Gate 1 Architecture Amendment Protocol)

## Architecture Amendment — `taskq.cli.main` dropped

- **When**: 2026-08-23T01:44:58.925113+00:00
- **Amended**: layer 'cli'
- **Reason**: No top-level cli dispatcher exists; the operational entry point is taskq.cli.key_create (python -m taskq.cli.key_create), so the planned main module is unreachable from the implemented surface.
- **Recorded by**: `harness_cli.py amend-sab --resolve-phantom` (Gate 1 Architecture Amendment Protocol)

## Architecture Amendment — `taskq.config.env` dropped

- **When**: 2026-08-23T01:44:59.716252+00:00
- **Amended**: layer 'config'
- **Reason**: Env loading is provided by Settings.from_env() inside taskq.config.settings; the planned taskq.config.env module was collapsed into settings.py during implementation, so a separate module entry would be empty.
- **Recorded by**: `harness_cli.py amend-sab --resolve-phantom` (Gate 1 Architecture Amendment Protocol)

## Architecture Amendment — `taskq.models.tag` dropped

- **When**: 2026-08-23T01:45:00.596059+00:00
- **Amended**: layer 'models'
- **Reason**: No Tag ORM model exists; the tags table is created by the v2 migration via raw op.create_table and is not read by any application code, so an empty models.tag module would be dead code under NFR-11.
- **Recorded by**: `harness_cli.py amend-sab --resolve-phantom` (Gate 1 Architecture Amendment Protocol)
