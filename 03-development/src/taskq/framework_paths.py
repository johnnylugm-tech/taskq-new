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
module paths the framework selected at P3. Each value is eagerly
validated via ``importlib.import_module`` at module load (see
below) so a typo in the path string fails loudly at import time
instead of silently breaking downstream role coverage.

This module exists solely to satisfy the FR-99 sentinel test
(``03-development/tests/test_fr99.py``); the four architectural
roles are otherwise covered by their own FR test suites. No
additional behaviour is owned by FR-99.

Citations: SPEC.md §10 「角色不變, 路徑變」; TEST_SPEC.md §FR-99;
TRACEABILITY_MATRIX.md §5 row 82.
"""
from __future__ import annotations

# pragma: no error-handling

import importlib

# Framework-owned path registry: role-name (FR-99 contract, frozen) ->
# dotted module path (framework's choice at Phase 3). The test asserts
# ``isinstance(FRAMEWORK_OWNED_ROLES, dict)`` so the concrete type is
# fixed; values are validated below at module-load time.
FRAMEWORK_OWNED_ROLES: dict[str, str] = {
    "auth_authz_layer": "taskq.service.auth",
    "tx_boundary_context_manager": "taskq.repository.units_of_work",
    "v3_data_migration_revision": "taskq.migrations.versions.v3_split_result_json_to_task_results",
    "async_subprocess_runner": "taskq.service.runner",
}

# Eager path validation: importing each registered dotted module here
# catches a typo in the path string at process start, before any
# downstream FR-04 / FR-06 / FR-07 / FR-08 code tries to resolve the
# same module path. The sentinel test re-validates the same paths, so
# this is a strict superset of its check.
def _validate_registry_paths() -> None:
    for _role_name, _module_path in FRAMEWORK_OWNED_ROLES.items():
        importlib.import_module(_module_path)

_validate_registry_paths()

__all__ = ["FRAMEWORK_OWNED_ROLES"]
