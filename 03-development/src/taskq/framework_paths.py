"""taskq.framework_paths — FR-99 framework-owned path registry.

[FR-99] Per SPEC.md §10 「角色不變, 路徑變」, FR-99 owns the ROLE
NAMES for the four architectural roles whose concrete module paths
the framework is free to choose at Phase 3:

  - ``auth_authz_layer``             -> FR-04 single decision layer
  - ``tx_boundary_context_manager``  -> FR-06 DB tx-boundary ctx mgr
  - ``v3_data_migration_revision``   -> FR-07 alembic revision file
  - ``async_subprocess_runner``      -> FR-08 async subprocess runner

The keys of ``FRAMEWORK_OWNED_ROLES`` are EXACTLY these four role
names (frozen contract); the values are the fully-qualified dotted
module paths the framework selected at P3. Each value MUST be
importable so downstream role coverage (FR-04 / FR-06 / FR-07 /
FR-08) can resolve the same module path the framework picked.

This module exists solely to satisfy the FR-99 sentinel test
(``03-development/tests/test_fr99.py``); the four architectural
roles are otherwise covered by their own FR test suites. No
additional behaviour is owned by FR-99.

Citations: SPEC.md §10 「角色不變, 路徑變」; TEST_SPEC.md §FR-99;
TRACEABILITY_MATRIX.md §5 row 82.
"""
from __future__ import annotations

# Framework-owned path registry: role-name (FR-99 contract, frozen) ->
# dotted module path (framework's choice at Phase 3). Each value is
# validated at test time via ``importlib.import_module`` so a typo
# in the path string fails loudly instead of silently breaking
# downstream role coverage.
FRAMEWORK_OWNED_ROLES: dict[str, str] = {
    "auth_authz_layer": "taskq.service.auth",
    "tx_boundary_context_manager": "taskq.repository.units_of_work",
    "v3_data_migration_revision": "taskq.migrations.versions.v3_split_result_json_to_task_results",
    "async_subprocess_runner": "taskq.service.runner",
}

__all__ = ["FRAMEWORK_OWNED_ROLES"]
