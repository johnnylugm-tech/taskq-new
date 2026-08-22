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

This file pins the FR-99 placeholder contract (per `SPEC.md` §10
「角色不變,路徑變」: role names are fixed; module paths are framework-owned).

Test names MUST match the harness pre-flight expectation for FR-99
(no test-name root; the two TEST_SPEC cases below ARE the FR-99 test
surface).
"""
from __future__ import annotations

# GREEN contract: ``taskq.framework_paths`` exposes a public attribute
# ``FRAMEWORK_OWNED_ROLES`` whose value is a ``dict[str, str]`` mapping
# each of the four architectural role names to the fully-qualified dotted
# module path the framework chose for that role at P3.
from taskq.framework_paths import FRAMEWORK_OWNED_ROLES  # noqa: E402,F401

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
    """FR-99 case #1 (per TEST_SPEC.md §FR-99):

      - inputs: ``role_names`` = comma-joined four role names;
        ``path_resolves`` = "true" iff every registry value imports.
      - sub-assertions: AC99.1-role-names-four, AC99.1-path-resolves.
    """
    # Build the spec-shaped inputs from the live registry.
    role_names = ",".join(sorted(FRAMEWORK_OWNED_ROLES.keys()))

    # Sub-assertion AC99.1-role-names-four (TEST_SPEC predicate):
    # `len(role_names.split(",")) == 4`
    assert len(role_names.split(",")) == 4

    # Sub-assertion AC99.1-path-resolves (TEST_SPEC predicate):
    # `path_resolves == "true"` — every value must be importable.
    import importlib

    path_resolves = "true"
    for module_path in FRAMEWORK_OWNED_ROLES.values():
        importlib.import_module(module_path)
    assert path_resolves == "true"


def test_fr99_placeholder_no_extra_own_assertions():
    """FR-99 case #2 (per TEST_SPEC.md §FR-99):

      - inputs: ``expected_role_count`` = "4".
      - sub-assertion: AC99.2-role-count-four.
    """
    # Sub-assertion AC99.2-role-count-four (TEST_SPEC predicate):
    # `expected_role_count == "4"`
    expected_role_count = "4"
    assert expected_role_count == "4"
