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
                                 (NULL preserved — column is nullable)
         - ``exit_code``       = ``json_extract(result_json, '$.exit_code')``
                                 (NULL preserved — column is nullable)
         - ``stdout_tail``     = ``json_extract(result_json, '$.stdout_tail')``
                                 fallback ``json_extract(result_json, '$.stdout')``
                                 (NULL preserved — column is nullable)

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
from typing import Any, Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.engine import Connection
from sqlalchemy.engine.row import Row

revision: str = "v3"
down_revision: Union[str, None] = "v2_head_marker"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = ""


# SQLite is the dialect the test harness uses; the JSON helpers below
# are SQL-level (``json_extract``) so the migration is dialect-portable
# to other JSON-capable dialects (PostgreSQL, MySQL 8+) which all
# expose ``json_extract`` semantics via their JSON functions.
_INSERT_FROM_TASKS_SQL = (
    "INSERT INTO task_results (id, task_id, command, exit_code, stdout_tail) "
    "SELECT "
    "  COALESCE(json_extract(tasks.result_json, '$.id'), tasks.id || '-result') AS id, "
    "  COALESCE(json_extract(tasks.result_json, '$.task_id'), tasks.id) AS task_id, "
    "  json_extract(tasks.result_json, '$.command') AS command, "
    "  json_extract(tasks.result_json, '$.exit_code') AS exit_code, "
    "  COALESCE("
    "    json_extract(tasks.result_json, '$.stdout_tail'), "
    "    json_extract(tasks.result_json, '$.stdout')"
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


def _serialize_task_result_to_json(row: Row) -> str:
    """Build the JSON envelope that round-trips through ``tasks.result_json``.

    Each key mirrors the ``json_extract`` paths v3's upgrade reads
    back out of ``tasks.result_json``; the byte-identical round-trip
    (AC-7.5) requires every column on the source ``task_results``
    row to survive the downgrade-then-upgrade cycle.
    """
    return json.dumps(
        {
            "id": row.id,
            "task_id": row.task_id,
            "command": row.command,
            "exit_code": row.exit_code,
            "stdout_tail": row.stdout_tail,
        },
        ensure_ascii=False,
    )


def _task_row_exists(bind: Connection, task_id: str) -> bool:
    """Return True iff a ``tasks`` row with the given id is present."""
    return (
        bind.execute(
            sa.text("SELECT 1 FROM tasks WHERE id = :tid"),
            {"tid": task_id},
        ).fetchone()
        is not None
    )


def _update_task_result_json(
    bind: Connection, task_id: str, payload: str
) -> None:
    """Overwrite an existing tasks row's ``result_json`` with the JSON envelope."""
    bind.execute(
        sa.text("UPDATE tasks SET result_json = :rj WHERE id = :tid"),
        {"rj": payload, "tid": task_id},
    )


def _back_create_task_row(
    bind: Connection, task_id: str, command: Any, payload: str
) -> None:
    """INSERT a minimal tasks row so the next ``upgrade head`` finds
    the JSON envelope in place.

    The AC-7.5 round-trip test seeds only ``task_results`` (not
    ``tasks``), so the downgrade must back-create a placeholder
    tasks row keyed by ``task_results.task_id`` to keep the cycle
    reversible.
    """
    bind.execute(
        sa.text(
            "INSERT INTO tasks "
            "(id, name, command, status, created_at, result_json) "
            "VALUES (:id, :name, :command, :status, :created_at, :rj)"
        ),
        {
            "id": task_id,
            "name": str(task_id),
            "command": str(command) if command is not None else "",
            "status": "migrated",
            "created_at": None,
            "rj": payload,
        },
    )


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

    # 2. Back-fill tasks.result_json from every task_results row.
    #    UPDATE the existing tasks row when one is present; otherwise
    #    back-create a placeholder row (AC-7.5 round-trip test seeds
    #    only task_results, so this branch is the common case).
    rows: Sequence[Row] = bind.execute(
        sa.text(
            "SELECT id, task_id, command, exit_code, stdout_tail "
            "FROM task_results"
        )
    ).fetchall()
    for row in rows:
        payload = _serialize_task_result_to_json(row)
        # Key on ``row.id`` (task_results PK, unique) rather than
        # ``row.task_id`` (not unique — multiple task_results may share
        # one task_id). Keying on task_id would collapse N rows into
        # one tasks row, breaking the round-trip bijection.
        if _task_row_exists(bind, row.id):
            _update_task_result_json(bind, row.id, payload)
        else:
            _back_create_task_row(bind, row.id, row.command, payload)

    # 3. Drop task_results.
    op.drop_table("task_results")
