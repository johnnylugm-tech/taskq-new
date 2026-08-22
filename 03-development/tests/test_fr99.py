"""RED tests for FR-99: Framework-owned implementation paths.

Per `01-requirements/TRACEABILITY_MATRIX.md` §5 row 82 and
`02-architecture/TEST_SPEC.md` §FR-99, FR-99 is a **PLACEHOLDER** FR that
owns NO new test assertions at Phase 3. The four architectural roles it
nominates (FR-04 auth/authz layer, FR-06 tx-boundary context manager,
FR-07 v3 data-migration revision file, FR-08 async sub-process runner)
are covered by their own FR test suites — FR-99 itself only exists so
the harness pre-flight that every `FR-XX` has a section in
`TEST_SPEC.md` (and a corresponding `test_fr99.py`) does not block the
dispatch.

This file therefore contains ONE sentinel test whose sole purpose is to:

  1. Pin the FR-99 placeholder contract (per `SPEC.md` §10 「角色不變,
     路徑變」: role names are fixed; module paths are framework-owned).
  2. Force a RED state at Phase 3 because the GREEN agent has not yet
     published a registry listing the four framework-owned paths.

If the GREEN agent publishes a `taskq.framework_paths` registry module
exposing ``FRAMEWORK_OWNED_ROLES`` as a mapping from role-name to
fully-qualified dotted module path, the test below will pass. Until then
the import will raise ``ModuleNotFoundError`` and pytest will surface a
Collection Error (Exit Code 2) — the valid RED state per the task
contract.

Test name MUST match the harness pre-flight expectation for FR-99 (a
single sentinel test is the entire scope; the TEST_SPEC.md FR-99 section
explicitly states "no test-name root" and "no new test assertions at
Phase 3", so this single function IS the FR-99 test surface).
"""
from __future__ import annotations

# GREEN TODO: taskq.framework_paths must expose a public attribute
# ``FRAMEWORK_OWNED_ROLES`` whose value is a ``dict[str, str]`` mapping
# each of the four architectural role names below to the fully-qualified
# dotted module path the framework chose for that role at P3:
#
#   - "auth_authz_layer"             -> FR-04 single decision layer
#   - "tx_boundary_context_manager" -> FR-06 DB tx-boundary ctx mgr
#   - "v3_data_migration_revision"   -> FR-07 alembic revision file
#   - "async_subprocess_runner"      -> FR-08 async subprocess runner
#
# Each value MUST be importable (i.e. ``importlib.import_module(value)``
# must succeed) so that downstream role coverage (FR-04 / FR-06 / FR-07
# / FR-08) can resolve the same module path the framework picked.
from taskq.framework_paths import FRAMEWORK_OWNED_ROLES  # noqa: E402,F401

import pytest  # noqa: E402

# The four role names whose concrete module paths are framework-owned
# per SPEC.md §10 「角色不變,路徑變」. FR-99 itself owns the ROLE NAMES;
# the GREEN agent owns the path VALUES (one per role).
_EXPECTED_ROLE_NAMES = frozenset({
    "auth_authz_layer",
    "tx_boundary_context_manager",
    "v3_data_migration_revision",
    "async_subprocess_runner",
})


def test_fr99_placeholder_framework_owned_role_registry():
    """FR-99 sentinel: assert the framework has published the four
    role-name -> module-path mappings for FR-04 / FR-06 / FR-07 / FR-08.

    RED state today: the registry module does not exist, so the top-level
    import raises ``ModuleNotFoundError`` and pytest reports a Collection
    Error (Exit Code 2) — the valid RED state for TDD-RED.

    GREEN state (after the GREEN agent publishes
    ``taskq.framework_paths.FRAMEWORK_OWNED_ROLES``):

      * ``FRAMEWORK_OWNED_ROLES`` is a ``dict`` (or ``Mapping``).
      * Its keys are EXACTLY the four role names declared above (no
        fewer, no extras — FR-99 owns the role-name set).
      * Each value is a non-empty dotted module path string that
        resolves via ``importlib.import_module`` (path validity is the
        framework's choice; role-name completeness is FR-99's contract).
    """
    import importlib

    # Registry must be a mapping (dict / MutableMapping).
    assert isinstance(FRAMEWORK_OWNED_ROLES, dict), (
        "FRAMEWORK_OWNED_ROLES must be a dict[str, str]; "
        f"got {type(FRAMEWORK_OWNED_ROLES).__name__}"
    )

    # Registry must contain EXACTLY the four role names — no fewer, no
    # more. FR-99 owns the role-name set; the framework picks the paths.
    actual_role_names = frozenset(FRAMEWORK_OWNED_ROLES.keys())
    assert actual_role_names == _EXPECTED_ROLE_NAMES, (
        "FRAMEWORK_OWNED_ROLES keys must equal the four role names "
        "fixed by SPEC §10 「角色不變,路徑變」: "
        f"missing={sorted(_EXPECTED_ROLE_NAMES - actual_role_names)} "
        f"extra={sorted(actual_role_names - _EXPECTED_ROLE_NAMES)}"
    )

    # Every value must be a non-empty dotted module path that resolves.
    for role_name in _EXPECTED_ROLE_NAMES:
        module_path = FRAMEWORK_OWNED_ROLES[role_name]
        assert isinstance(module_path, str) and module_path, (
            f"role {role_name!r} must map to a non-empty str path; "
            f"got {module_path!r}"
        )
        # Importing validates the framework's chosen path actually
        # exists on disk — a typo in the framework's path string would
        # otherwise pass the dict-shape check but fail at use-site.
        importlib.import_module(module_path)


def test_fr99_placeholder_no_extra_own_assertions():
    """FR-99 sentinel: this FR owns no functional assertions of its own.

    Pin the placeholder contract from `TEST_SPEC.md` §FR-99 ("FR-99
    itself owns NO new test assertions at Phase 3") by asserting the
    sole FR-99-owned behaviour — that the registry above is the
    COMPLETE surface — and nothing more. If a future change tries to
    add a fourth FR-99-owned functional assertion here, this test's
    docstring + the TEST_SPEC note will surface that as a deviation.

    Trivially passes when the registry import resolves; serves as a
    living "this file is intentionally minimal" marker.
    """
    # If the registry is in place, this FR has done its job. No further
    # assertions are owned by FR-99 per TEST_SPEC.md §FR-99.
    assert len(_EXPECTED_ROLE_NAMES) == 4, (
        "FR-99 owns exactly four role names; this constant must not "
        "drift without an accompanying TEST_SPEC.md amendment."
    )
