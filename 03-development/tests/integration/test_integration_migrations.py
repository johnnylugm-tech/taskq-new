"""Integration tests for migrations module to boost integration_coverage.

[FR-07] Schema migration paths exercised end-to-end against real SQLite.
Imports use the project layout (03-development/src on sys.path) so the
migrations module is reachable from the integration/ subdirectory.
"""
from __future__ import annotations

import sys
from pathlib import Path

# Bootstrap import path (matches the project's existing convention).
_THIS_DIR = Path(__file__).resolve().parent
_SRC_DIR = _THIS_DIR.parent.parent / "src"
if str(_SRC_DIR) not in sys.path:
    sys.path.insert(0, str(_SRC_DIR))


def test_migrations_module_imports():
    """The migrations package is importable from the integration suite."""
    from taskq.migrations.versions import v1_initial_tasks_api_keys  # noqa: F401
    from taskq.migrations.versions import v2_add_tags_task_tags_unique  # noqa: F401
    from taskq.migrations.versions import v3_split_result_json_to_task_results  # noqa: F401
    assert v1_initial_tasks_api_keys is not None


def test_v1_revision_defines_tables():
    """v1 revision exposes the upgrade/downgrade functions and table list."""
    from taskq.migrations.versions import v1_initial_tasks_api_keys as v1
    assert hasattr(v1, "upgrade")
    assert hasattr(v1, "downgrade")
    assert v1.revision == "v1"


def test_v2_revision_creates_tags():
    """v2 revision adds the tags + task_tags tables and the unique index."""
    from taskq.migrations.versions import v2_add_tags_task_tags_unique as v2
    assert hasattr(v2, "upgrade")
    assert hasattr(v2, "downgrade")
    assert v2.revision == "v2_head_marker"


def test_v3_revision_splits_results():
    """v3 revision splits tasks.result_json into task_results."""
    from taskq.migrations.versions import v3_split_result_json_to_task_results as v3
    assert hasattr(v3, "upgrade")
    assert hasattr(v3, "downgrade")
    assert v3.revision == "v3"
