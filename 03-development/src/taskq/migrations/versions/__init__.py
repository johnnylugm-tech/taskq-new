"""taskq.migrations.versions — three alembic revisions (v1, v2, v3).

[FR-07] The SAB binds this module's dotted name to FR-07; Gate 1's
Architecture Amendment Protocol blocks phantom modules. The ``v1``,
``v2``, ``v3`` aliases are imported by the test harness:

    from taskq.migrations.versions import v1, v2, v3

Each alias is the ``revision`` identifier string assigned by alembic
(or any truthy value), kept as a small named constant so the import
in ``tests/test_fr07.py`` resolves even before alembic discovers the
files via ``ScriptDirectory``.

The actual alembic discovery (used by ``command.upgrade`` /
``command.downgrade``) is driven by the *.py files in this directory:

  - v1_initial_tasks_api_keys.py            -> revision id "v1"
  - v2_add_tags_task_tags_unique.py         -> revision id "v2"
  - v3_split_result_json_to_task_results.py -> revision id "v3"

The chain is v1 (initial) -> v2 (tags + unique index) -> v3 (split
result_json -> task_results data-migration). Every step is reversible
on a real SQLite file (NFR-09).

Citations: SPEC.md §3 FR-07; SAD.md §3.4; TEST_SPEC.md FR-07.
"""
from __future__ import annotations

from . import (
    v1_initial_tasks_api_keys,
    v2_add_tags_task_tags_unique,
    v3_split_result_json_to_task_results,
)

# Stable revision identifiers — these are the strings alembic records
# in the ``alembic_version`` table. Each module file declares the
# matching ``revision = "v1"`` (etc.) literal; the ``versions``
# package surface simply re-exports them so the SAB-mandated dotted
# import resolves.
v1: str = v1_initial_tasks_api_keys.revision
v2: str = v2_add_tags_task_tags_unique.revision
v3: str = v3_split_result_json_to_task_results.revision

__all__ = ["v1", "v2", "v3"]
