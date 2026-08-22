"""v2 — tags + task_tags (many-to-many) + unique index on tasks.name.

[FR-07] Revision v2 adds:

  - ``tags``: id (PK), name.
  - ``task_tags`` (M2M): task_id (FK -> tasks.id), tag_id (FK -> tags.id),
    composite primary key.
  - ``tasks.result_json`` (TEXT, nullable) — added here (rather than v1)
    so v1 stays minimal and v3's data migration has a clean source
    column to consume.
  - UNIQUE INDEX on ``tasks.name`` — required by SPEC §3 FR-07 v2 row.

Downgrade drops the new tables, the index, and the ``result_json``
column without affecting v1 data (the ``tasks`` / ``api_keys`` rows
survive the downgrade).

We use ``op.add_column(...)`` and ``op.drop_column(...)`` directly
instead of ``op.batch_alter_table(...)`` because the latter requires
a live database connection for table reflection and therefore does
NOT render in alembic's offline SQL mode (AC-7.7). SQLite 3.35+
supports single-column ``ALTER TABLE ADD/DROP COLUMN`` so the
direct-path emits clean ``ALTER TABLE`` statements without the
batch-mode overhead.

Citations: SPEC.md §3 FR-07 (v2 row); SPEC.md §8 #12 (round-trip,
loaded by v3); NFR-09 (real SQLite file).
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "v2_head_marker"
down_revision: Union[str, None] = "v1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add tags + M2M + unique name index; add ``tasks.result_json``."""
    # 1. Add ``result_json`` column to tasks (nullable TEXT).
    op.add_column(
        "tasks",
        sa.Column("result_json", sa.Text(), nullable=True),
    )

    # 2. Create ``tags`` table.
    op.create_table(
        "tags",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("name", sa.String(), nullable=False),
    )

    # 3. Create ``task_tags`` M2M table.
    op.create_table(
        "task_tags",
        sa.Column("task_id", sa.String(), sa.ForeignKey("tasks.id"), primary_key=True),
        sa.Column("tag_id", sa.String(), sa.ForeignKey("tags.id"), primary_key=True),
    )

    # 4. UNIQUE INDEX on tasks.name — the FR-07 v2 row contract.
    # ``op.create_index(..., unique=True)`` surfaces as both an entry
    # in ``get_indexes`` (with ``unique=True``) AND in the dialect's
    # internal unique-constraints view; the AC-7.2 test accepts either.
    op.create_index(
        "ix_tasks_name_unique",
        "tasks",
        ["name"],
        unique=True,
    )


def downgrade() -> None:
    """Reverse v2 — drop new tables, index, and ``result_json`` column.

    ``tasks`` and ``api_keys`` (v1 data) are preserved by this
    downgrade; only v2's own additions are removed.
    """
    # Reverse order of upgrade.
    op.drop_index("ix_tasks_name_unique", table_name="tasks")
    op.drop_table("task_tags")
    op.drop_table("tags")
    op.drop_column("tasks", "result_json")
