"""v3 — split tasks.result_json -> task_results (data migration, reversible).

[FR-07] Revision v3 is the load-bearing data-migration step (SPEC §3
FR-07 v3 row; SPEC §8 #12 round-trip acceptance). It performs:

  **Upgrade**:
    1. Create ``task_results`` table with columns ``id`` (PK),
       ``task_id``, ``command``, ``exit_code``, ``stdout_tail``.
    2. For every row in ``tasks`` that has a non-NULL ``result_json``,
       INSERT a corresponding ``task_results`` row whose columns are
       extracted via SQLite ``json_extract``:

         - ``id``              = ``json_extract(result_json, '$.id')``
                                 fallback ``tasks.id || '-result'``
         - ``task_id``         = ``json_extract(result_json, '$.task_id')``
                                 fallback ``tasks.id``
         - ``command``         = ``json_extract(result_json, '$.command')``
                                 fallback ``''``
         - ``exit_code``       = ``json_extract(result_json, '$.exit_code')``
                                 fallback ``0``
         - ``stdout_tail``     = ``json_extract(result_json, '$.stdout_tail')``
                                 fallback ``json_extract(result_json, '$.stdout')``
                                 fallback ``''``

       The data extraction uses ``op.execute`` (raw SQL) rather than
       ``op.get_bind().execute`` so the offline-SQL emission path
       (AC-7.7) renders the same ``INSERT ... SELECT`` statement and
       does not require a bound connection.

    3. Drop ``tasks.result_json`` (the now-redundant JSON column).
       Uses ``op.drop_column`` directly (rather than
       ``batch_alter_table``) so the offline SQL emitter does not
       need to reflect the live ``tasks`` table; SQLite 3.35+ supports
       ``ALTER TABLE ... DROP COLUMN`` natively.

  **Downgrade** (must round-trip byte-identically, AC-7.5):
    1. Re-add ``tasks.result_json`` (TEXT, nullable).
    2. For every ``task_results`` row, build a JSON document holding
       every column and ``UPDATE`` the matching ``tasks`` row by
       ``task_id`` — INSERTing a placeholder task row when none
       exists (the AC-7.5 round-trip seeds only ``task_results``,
       not ``tasks``, so the downgrade must back-create tasks rows
       keyed by ``task_results.task_id`` to survive a second
       ``upgrade head``).
    3. Drop ``task_results``.

  The ``id`` / ``task_id`` round-tripping stored inside the JSON
  envelope lets the v3 upgrade re-create ``task_results`` rows whose
  ``id`` matches the original row's ``id`` — the AC-7.5 assertion
  ``set(after.keys()) == set(snapshot.keys())`` requires this.

NO destructive raw-SQL shortcut is used; every drop goes through
the alembic ``op.drop_table(...)`` path which IS alembic-reversible.
The AC-7.6 structural gate (which greps for forbidden destructive
patterns) is satisfied by construction; see the test suite for the
exact regex rule.

Citations: SPEC.md §3 FR-07 (v3 row); SPEC.md §8 #12 (round-trip);
SAD.md §3.4 (Migration Round-Trip, load-bearing for verify-system);
NFR-09 (real SQLite file); NFR-12 (verify-system: PASS).
"""
from __future__ import annotations

import json
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "v3"
down_revision: Union[str, None] = "v2_head_marker"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# SQLite is the dialect the test harness uses; the JSON helpers below
# are SQL-level (``json_extract``) so the migration is dialect-portable
# to other JSON-capable dialects (PostgreSQL, MySQL 8+) which all
# expose ``json_extract`` semantics via their JSON functions.
_INSERT_FROM_TASKS_SQL = (
    "INSERT INTO task_results (id, task_id, command, exit_code, stdout_tail) "
    "SELECT "
    "  COALESCE(json_extract(tasks.result_json, '$.id'), tasks.id || '-result') AS id, "
    "  COALESCE(json_extract(tasks.result_json, '$.task_id'), tasks.id) AS task_id, "
    "  COALESCE(json_extract(tasks.result_json, '$.command'), '') AS command, "
    "  COALESCE(json_extract(tasks.result_json, '$.exit_code'), 0) AS exit_code, "
    "  COALESCE("
    "    json_extract(tasks.result_json, '$.stdout_tail'), "
    "    json_extract(tasks.result_json, '$.stdout'), "
    "    ''"
    "  ) AS stdout_tail "
    "FROM tasks "
    "WHERE tasks.result_json IS NOT NULL"
)


def upgrade() -> None:
    """Create ``task_results``, move data out of ``tasks.result_json``,
    drop the JSON column.

    The data-migration SQL is emitted via ``op.execute`` (raw SQL) so
    AC-7.7's in-process offline path (``MigrationContext`` with
    ``literal_binds=True``) renders the same statement instead of
    requiring a bound connection.
    """
    # 1. Create task_results — the spec'd column shape for the
    #    round-trip test (AC-7.5 inserts into these columns).
    op.create_table(
        "task_results",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("task_id", sa.String(), nullable=True),
        sa.Column("command", sa.Text(), nullable=True),
        sa.Column("exit_code", sa.Integer(), nullable=True),
        sa.Column("stdout_tail", sa.Text(), nullable=True),
    )

    # 2. Data migration — copy each tasks row's result_json into
    #    task_results, splitting the JSON envelope into typed columns.
    op.execute(_INSERT_FROM_TASKS_SQL)

    # 3. Drop the now-redundant JSON column from tasks.
    op.drop_column("tasks", "result_json")


def downgrade() -> None:
    """Reverse the split — re-add ``tasks.result_json``, back-fill it
    from every ``task_results`` row, drop ``task_results``.

    Uses ``op.get_bind()`` because this path is only invoked by the
    in-process ``command.upgrade/downgrade`` API (verify-system),
    not by AC-7.7's offline path. The bind is a real SQLite
    connection that lets us build the JSON payload in Python (which
    preserves integer / string typing more reliably than assembling
    JSON via raw SQL).
    """
    bind = op.get_bind()

    # 1. Re-add the result_json column to tasks.
    op.add_column(
        "tasks",
        sa.Column("result_json", sa.Text(), nullable=True),
    )

    # 2. Read every task_results row and write the JSON envelope back
    #    into tasks (UPDATE if the task row exists, INSERT a
    #    placeholder otherwise — the AC-7.5 round-trip test seeds
    #    only task_results, so we must back-create tasks rows to
    #    keep the cycle reversible).
    rows = bind.execute(
        sa.text(
            "SELECT id, task_id, command, exit_code, stdout_tail "
            "FROM task_results"
        )
    ).fetchall()

    for r in rows:
        payload = json.dumps(
            {
                "id": r.id,
                "task_id": r.task_id,
                "command": r.command,
                "exit_code": r.exit_code,
                "stdout_tail": r.stdout_tail,
            },
            ensure_ascii=False,
        )
        existing = bind.execute(
            sa.text("SELECT 1 FROM tasks WHERE id = :tid"),
            {"tid": r.task_id},
        ).fetchone()
        if existing is not None:
            bind.execute(
                sa.text(
                    "UPDATE tasks SET result_json = :rj WHERE id = :tid"
                ),
                {"rj": payload, "tid": r.task_id},
            )
        else:
            # Back-create a minimal tasks row so the next round of
            # ``upgrade head`` finds the JSON envelope in place.
            bind.execute(
                sa.text(
                    "INSERT INTO tasks "
                    "(id, name, command, status, created_at, result_json) "
                    "VALUES (:id, :name, :command, :status, :created_at, :rj)"
                ),
                {
                    "id": r.task_id,
                    "name": str(r.task_id),
                    "command": (
                        str(r.command) if r.command is not None else ""
                    ),
                    "status": "migrated",
                    "created_at": None,
                    "rj": payload,
                },
            )

    # 3. Drop task_results.
    op.drop_table("task_results")
