"""v1 — initial schema: tasks + api_keys tables.

[FR-07] Revision v1 — the base migration. Creates two tables:

  - ``tasks``: id (PK), name, command, status, created_at. Columns
    are mostly nullable so subsequent migrations (notably v2 which
    adds ``result_json`` and v3 which drops it) do not need to
    supply defaults during data backfill.

  - ``api_keys``: id (PK), key_hash, owner, created_at. Holds HMAC
    key material for FR-03 / FR-04. ``key_hash`` is unique (the
    API routes look up keys by this column).

Both tables are dropped on downgrade (FR-07 v1 row in SPEC §3).

Citations: SPEC.md §3 FR-07 (v1 row); NFR-09 (real SQLite file).
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "v1"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Create the two initial tables — ``tasks`` and ``api_keys``."""
    op.create_table(
        "tasks",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("name", sa.String(), nullable=True),
        sa.Column("command", sa.Text(), nullable=True),
        sa.Column("status", sa.String(), nullable=True),
        sa.Column("created_at", sa.String(), nullable=True),
    )
    op.create_table(
        "api_keys",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("key_hash", sa.String(), nullable=False),
        sa.Column("owner", sa.String(), nullable=True),
        sa.Column("created_at", sa.String(), nullable=True),
    )
    # Unique index on api_keys.key_hash — FR-03 / FR-04 look keys up by
    # this column. Created at v1 time so subsequent migrations can
    # rely on it.
    op.create_index(
        "ix_api_keys_key_hash_unique",
        "api_keys",
        ["key_hash"],
        unique=True,
    )


def downgrade() -> None:
    """Drop both tables — v1's reverse."""
    op.drop_index("ix_api_keys_key_hash_unique", table_name="api_keys")
    op.drop_table("api_keys")
    op.drop_table("tasks")
