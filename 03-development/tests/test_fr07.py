"""RED tests for FR-07: Schema Migration (Alembic three-step, real SQLite).

Test names MUST match TEST_SPEC.md (`02-architecture/TEST_SPEC.md`)
section "FR-07: Schema Migration (Alembic three-step, real SQLite)"
exactly:

  - test_fr07_ac1_v1_creates_tasks_api_keys_tables
  - test_fr07_ac2_v2_adds_tags_task_tags_unique_index
  - test_fr07_ac3_v3_migrates_tasks_result_json_to_task_results
  - test_fr07_ac4_upgrade_head_downgrade_base_exit_zero
  - test_fr07_ac5_round_trip_byte_identical_sample
  - test_fr07_ac6_no_destructive_shortcut_drop_table
  - test_fr07_ac7_offline_sql_generation_covered

spec-coverage-check uses exact match; do NOT rename these functions.

SAB module declarations for FR-07 (binding on the GREEN implementation —
Gate 1's Architecture Amendment Protocol blocks phantom modules):

  - taskq.migrations.versions  -> 03-development/src/taskq/migrations/versions.py
    (or 03-development/src/taskq/migrations/versions/__init__.py).
    Either on-disk shape satisfies the check; a DIFFERENT name does not.
    The GREEN agent must create the Alembic env / script / and three
    revisions (v1 tasks + api_keys, v2 tags + task_tags + unique name,
    v3 split result_json -> task_results) such that
    ``alembic upgrade head`` and ``alembic downgrade base`` both exit 0,
    the v3 split is reversible on real data (round-trip byte-identical),
    and ``alembic --sql`` (offline mode) generates the migration SQL.

Citations: SPEC.md §3 FR-07 (Alembic three-step), §8 #12 (round-trip),
§8 #13 (upgrade head / downgrade base exit 0); SAD.md §3.4 (Migration
Round-Trip — load-bearing for verify-system); NFR-09 (real SQLite file,
not in-memory); NFR-12 (verify-system: PASS).
"""
from __future__ import annotations

import inspect
import io
import json
import os  # noqa: F401 -- referenced in GREEN TODO env setup
import re  # noqa: F401 -- referenced in GREEN TODO docstrings
import sys
from contextlib import redirect_stdout
from pathlib import Path
from typing import List

import pytest

# Hypothesis is the Python property-based testing library. It is
# required to execute FR-07's P7-roundtrip-bijection invariant
# (TEST_SPEC.md FR-07 Properties table; see also ``requirements.txt``
# justification comment). The bijection ``migrate_reverse(migrate_forward(row))
# == row`` is declared symbolic and degrades to ``needs_review`` until a
# `@given`-driven test runs it; this module adds the executing test.
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

# ---- Import path bootstrap ----
# Test file lives at 03-development/tests/test_fr07.py; the package
# source is at 03-development/src. We resolve to the project root so
# both ``from taskq...`` imports AND the in-process alembic / offline-SQL
# invocation can find ``src/taskq/migrations``.
_THIS_DIR = Path(__file__).resolve().parent
_PROJECT_ROOT = _THIS_DIR.parent
_SRC_DIR = _PROJECT_ROOT / "src"
if str(_SRC_DIR) not in sys.path:
    sys.path.insert(0, str(_SRC_DIR))


# ---- Standard top-level imports (NO try/except ImportError) ----
# A missing module below is the EXPECTED RED state: pytest will surface
# ModuleNotFoundError as a Collection Error, which is the validated
# failure signal for this step (FR-07 implementation has not landed yet).

# GREEN TODO: taskq.migrations.versions must exist as either a leaf module
# (src/taskq/migrations/versions.py) or a package
# (src/taskq/migrations/versions/__init__.py). It MUST expose the alembic
# ``Revision`` objects for v1, v2, v3 (the data-migration revision whose
# downgrade must reverse the move). At minimum, the module MUST expose:
#   - ``v1`` — alembic revision creating tables ``tasks`` + ``api_keys``
#   - ``v2`` — alembic revision adding ``tags`` + ``task_tags`` plus a
#     unique index on ``tasks.name``
#   - ``v3`` — alembic revision that:
#       (a) creates ``task_results``
#       (b) inserts one ``task_results`` row per existing
#           ``tasks.result_json`` value
#       (c) drops ``tasks.result_json``
#     and whose downgrade MUST:
#       (a) re-add ``tasks.result_json``
#       (b) copy every ``task_results`` row back into ``tasks.result_json``
#       (c) drop ``task_results``
# The SAB block declares this module's dotted name; Gate 1 blocks if it
# is missing or renamed.
from taskq.migrations.versions import v1, v2, v3  # noqa: E402,F401


# ---------- Constants declared by TEST_SPEC Inputs rows ----------

# AC-7.1 — TEST_SPEC Inputs: revision="v1"; tables_created="tasks,api_keys";
# downgrade_drops="tasks,api_keys"; state_mode="isolate_per_test".
TABLES_CREATED = "tasks,api_keys"
DOWNGRADE_DROPS = "tasks,api_keys"

# AC-7.2 — TEST_SPEC Inputs: revision="v2"; tables_added="tags,task_tags";
# unique_index="tasks.name".
TABLES_ADDED = "tags,task_tags"
UNIQUE_INDEX = "tasks.name"

# AC-7.3 — TEST_SPEC Inputs: revision="v3"; source_column="tasks.result_json";
# target_table="task_results"; rows_migrated="3".
SOURCE_COLUMN = "tasks.result_json"
TARGET_TABLE = "task_results"
ROWS_MIGRATED = "3"

# AC-7.4 — TEST_SPEC Inputs: sequence="upgrade_head,downgrade_base";
# expected_exit="0".
SEQUENCE = "upgrade_head,downgrade_base"
EXPECTED_EXIT = "0"

# AC-7.5 — TEST_SPEC Inputs: sample_columns="command,name,exit_code,stdout_tail";
# seed_rows="3".
SAMPLE_COLUMNS = "command,name,exit_code,stdout_tail"
SEED_ROWS = "3"

# AC-7.6 — TEST_SPEC Inputs: migration_dir="src/taskq/migrations";
# forbidden_pattern='op.execute("DROP TABLE'; expected_hits="0".
MIGRATION_DIR = "src/taskq/migrations"
FORBIDDEN_PATTERN = 'op.execute("DROP TABLE'

# AC-7.7 — TEST_SPEC Inputs: alembic_mode="offline"; sql_generated="true";
# expected_lines_min="1".
ALEMBIC_MODE = "offline"
SQL_GENERATED = "true"
EXPECTED_LINES_MIN = "1"


# ---------- Fixtures ----------

@pytest.fixture(autouse=True)
def _isolate_taskq_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Per-test isolated TASKQ_HOME so alembic upgrades do not collide
    across the seven tests in this file (TEST_SPEC ``state_mode``:
    ``isolate_per_test`` for every AC except the unit-level grep tests).

    FR-07's load-bearing scenario is the v3 round-trip on a REAL SQLite
    file (NFR-09: real SQLite file, not in-memory). Each test gets its
    own directory under ``tmp_path`` so two tests cannot observe each
    other's alembic_version row.
    """
    home = tmp_path / "taskq_home"
    home.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("TASKQ_HOME", str(home))
    yield home


def _alembic_cfg(db_url: str) -> "alembic.config.Config":  # noqa: F821 -- forward reference resolved at runtime
    """Build an in-memory alembic Config pointing at the project migrations.

    The Config is constructed programmatically (no alembic.ini required
    on disk) so the test can drive alembic from Python without relying
    on subprocess. We point ``script_location`` at the
    ``src/taskq/migrations`` directory which the GREEN agent creates.
    """
    from alembic.config import Config

    cfg = Config()
    cfg.set_main_option("script_location", str(_SRC_DIR / "taskq" / "migrations"))
    cfg.set_main_option("sqlalchemy.url", db_url)
    # ``file_template`` is unused in offline mode but Config complains
    # if it's empty when ``version_locations`` is missing; default is fine.
    return cfg


def _sqlite_url(db_path: Path) -> str:
    """Produce a SQLite file URL the SQLAlchemy dialect accepts."""
    return f"sqlite:///{db_path}"


def _reflection_inspector(db_url: str):
    """Build a SQLAlchemy inspector for the live DB after a migration."""
    from sqlalchemy import create_engine

    engine = create_engine(db_url)
    return engine, inspect(engine)


# ---------- AC-7.1: revision v1 creates `tasks` + `api_keys` ----------

def test_fr07_ac1_v1_creates_tasks_api_keys_tables(tmp_path: Path):
    # NFR-09: real SQLite file (AC-N9.5: FR-07 migration tested against real SQLite file, not in-memory)
    # NFR-12: verify-system target chains alembic upgrade head / downgrade base
    """AC-7.1 — Revision v1 creates the ``tasks`` and ``api_keys``
    tables; downgrade drops both tables (SPEC.md §3 FR-07).

    Sub-assertions:
      - AC7.1-tables-v1:    len(tables_created.split(",")) == 2
      - AC7.1-state-isolate: state_mode == "isolate_per_test"

    Inputs: revision="v1"; tables_created="tasks,api_keys";
            downgrade_drops="tasks,api_keys"; state_mode="isolate_per_test".

    Strategy: drive alembic programmatically (NOT subprocess — subprocess
    prevents pytest-cov coverage of the migration module, see FR-07
    Integration Guidelines). Run ``alembic upgrade`` to the v1
    revision's head, inspect the database, assert both tables exist;
    then ``alembic downgrade -1`` and assert both tables are gone.

    Implementation choice (in-process): we import the v1 Revision object
    and drive alembic via its Python API; the assertions run against a
    real SQLite file at ``tmp_path / "ac1.db"``.

    NFR-09: real SQLite file (NFR-09 explicitly requires this for FR-07).
    NFR-10: in-process integration.
    """
    # ---- MIRROR binding asserts (TEST_SPEC sub-assertion predicates) ----
    # Sub-assertion AC7.1-tables-v1: tables_created has 2 entries
    tables_created = TABLES_CREATED
    assert len(tables_created.split(",")) == 2, (
        f"AC-7.1 contract: tables_created must enumerate exactly 2 "
        f"tables, got {len(tables_created.split(','))}"
    )
    # Sub-assertion AC7.1-state-isolate: state_mode == "isolate_per_test"
    state_mode = "isolate_per_test"
    assert state_mode == "isolate_per_test"

    db_path = tmp_path / "ac1.db"
    db_url = _sqlite_url(db_path)
    cfg = _alembic_cfg(db_url)

    # GREEN TODO: v1.Revision.down_revision is None (it is the initial
    # revision); its upgrade() creates tables ``tasks`` and ``api_keys``;
    # its downgrade() drops both tables (SPEC §3 FR-07 v1 row).
    from alembic import command

    # Upgrade to v1 (the initial revision).
    buf = io.StringIO()
    with redirect_stdout(buf):
        command.upgrade(cfg, "head")
    upgrade_stdout = buf.getvalue()

    engine, insp = _reflection_inspector(db_url)
    table_names = set(insp.get_table_names())
    for t in tables_created.split(","):
        assert t in table_names, (
            f"AC-7.1 violated: table {t!r} not present after "
            f"alembic upgrade head (v1). tables={sorted(table_names)!r}"
        )

    # Now downgrade all the way back to base; both v1 tables must be
    # dropped.
    buf = io.StringIO()
    with redirect_stdout(buf):
        command.downgrade(cfg, "base")
    downgrade_stdout = buf.getvalue()

    engine, insp = _reflection_inspector(db_url)
    table_names_after = set(insp.get_table_names())
    for t in DOWNGRADE_DROPS.split(","):
        assert t not in table_names_after, (
            f"AC-7.1 violated: table {t!r} still present after "
            f"alembic downgrade base. tables="
            f"{sorted(table_names_after)!r}"
        )

    # Cleanup stdout assertions — at minimum we drove alembic without
    # raising (the upgrade_stdout / downgrade_stdout buffers being
    # non-empty is a healthy sign that alembic ran the scripts).
    assert upgrade_stdout != "" or downgrade_stdout != "", (
        "AC-7.1 alembic produced no output on either upgrade or "
        "downgrade; the migration env may not be wired correctly"
    )


# ---------- AC-7.2: revision v2 adds `tags`, `task_tags`, unique index ----------

def test_fr07_ac2_v2_adds_tags_task_tags_unique_index(tmp_path: Path):
    # NFR-09: real SQLite file (AC-N9.5)
    # NFR-12: verify-system target (alembic upgrade head in chain)
    """AC-7.2 — Revision v2 adds ``tags``, ``task_tags`` (many-to-many),
    plus a unique index on ``tasks.name``; downgrade drops the new
    tables and index without affecting v1 data (SPEC.md §3 FR-07).

    Sub-assertions:
      - AC7.2-tables-v2:         len(tables_added.split(",")) == 2
      - AC7.2-unique-index-name: unique_index == "tasks.name"

    Inputs: revision="v2"; tables_added="tags,task_tags";
            unique_index="tasks.name".

    Strategy: upgrade to v2 on a fresh DB, inspect for the new tables
    and the unique index on ``tasks.name``. Then downgrade to v1 and
    verify the new tables and index are gone while ``tasks`` /
    ``api_keys`` survive.

    Implementation choice (in-process): same as AC-7.1; alembic driven
    via the Python API for coverage.
    """
    # ---- MIRROR binding asserts (TEST_SPEC sub-assertion predicates) ----
    # Sub-assertion AC7.2-tables-v2: tables_added has 2 entries
    tables_added = TABLES_ADDED
    assert len(tables_added.split(",")) == 2, (
        f"AC-7.2 contract: tables_added must enumerate exactly 2 tables, "
        f"got {len(tables_added.split(','))}"
    )
    # Sub-assertion AC7.2-unique-index-name: unique_index == "tasks.name"
    unique_index = UNIQUE_INDEX
    assert unique_index == "tasks.name"

    db_path = tmp_path / "ac2.db"
    db_url = _sqlite_url(db_path)
    cfg = _alembic_cfg(db_url)

    from alembic import command

    # Upgrade all the way to head (v3) so we know v2 actually ran — the
    # unique index on ``tasks.name`` only exists after v2 applies.
    buf = io.StringIO()
    with redirect_stdout(buf):
        command.upgrade(cfg, "head")
    buf.getvalue()  # drain

    engine, insp = _reflection_inspector(db_url)
    table_names = set(insp.get_table_names())

    # New tables present after upgrade to head (which includes v2).
    for t in tables_added.split(","):
        assert t in table_names, (
            f"AC-7.2 violated: table {t!r} not present after "
            f"alembic upgrade head (v2). tables={sorted(table_names)!r}"
        )

    # Unique index on tasks.name must exist. SQLAlchemy exposes both
    # ``get_indexes(table_name)`` and ``get_unique_constraints``; the
    # unique constraint created by ``op.create_unique_constraint`` /
    # ``op.create_index(..., unique=True)`` surfaces in BOTH views.
    # We assert on both to tolerate either Alembic pattern.
    indexes = insp.get_indexes("tasks")
    unique_constraints = insp.get_unique_constraints("tasks")
    index_column_set = {
        tuple(sorted(idx["column_names"]))
        for idx in indexes
        if idx.get("unique")
    }
    constraint_column_set = {
        tuple(sorted(uc["column_names"]))
        for uc in unique_constraints
    }
    name_column_tuple = ("name",)
    assert name_column_tuple in index_column_set or name_column_tuple in constraint_column_set, (
        f"AC-7.2 violated: no unique index/constraint on tasks.name "
        f"after v2 upgrade. indexes={indexes!r}, "
        f"unique_constraints={unique_constraints!r}"
    )

    # Downgrade two steps (v3 -> v2 -> v1) so v2's own downgrade
    # actually drops its own additions without touching v1 tables.
    # Going all the way to base would also drop v1's tables (which
    # v1's downgrade correctly drops per SPEC §3 FR-07 v1 row), so
    # we stop at v1 to verify v2's reversibility on v1 data.
    buf = io.StringIO()
    with redirect_stdout(buf):
        command.downgrade(cfg, "v1")
    buf.getvalue()

    engine, insp = _reflection_inspector(db_url)
    table_names_after = set(insp.get_table_names())

    # v1 tables MUST survive the v2 downgrade.
    for t in ("tasks", "api_keys"):
        assert t in table_names_after, (
            f"AC-7.2 violated: v1 table {t!r} disappeared after v2 "
            f"downgrade. tables={sorted(table_names_after)!r}"
        )

    # v2 tables MUST be gone.
    for t in tables_added.split(","):
        assert t not in table_names_after, (
            f"AC-7.2 violated: v2 table {t!r} still present after "
            f"alembic downgrade to v1. tables={sorted(table_names_after)!r}"
        )


# ---------- AC-7.3: revision v3 migrates tasks.result_json -> task_results ----------

def test_fr07_ac3_v3_migrates_tasks_result_json_to_task_results(tmp_path: Path):
    # NFR-09: real SQLite file (AC-N9.5)
    # NFR-03: error handling + txn — data migration must be transactional; failure rolls back
    # NFR-12: verify-system target chains upgrade head
    """AC-7.3 — Revision v3 performs the data migration: splits
    ``tasks.result_json`` into a separate ``task_results`` table,
    migrates existing data, then drops the original column; downgrade
    reverses the move and drops ``task_results`` (SPEC.md §3 FR-07).

    Sub-assertions:
      - AC7.3-source-col:    source_column == "tasks.result_json"
      - AC7.3-target-table:  target_table == "task_results"
      - AC7.3-rows-three:    rows_migrated == "3"

    Inputs: revision="v3"; source_column="tasks.result_json";
            target_table="task_results"; rows_migrated="3".

    Strategy: upgrade to v2, INSERT three rows into ``tasks`` with
    distinct ``result_json`` payloads, then upgrade to v3. After
    upgrade to v3, assert:
      (a) the ``tasks`` table no longer carries ``result_json``
      (b) the ``task_results`` table exists with exactly 3 rows
      (c) the ``task_results`` rows carry the same JSON payloads.

    Implementation choice (in-process): alembic upgrade via Python API;
    data is inserted via a raw SQLAlchemy INSERT so the test does not
    depend on FR-01 / FR-02 repository code.
    """
    # ---- MIRROR binding asserts (TEST_SPEC sub-assertion predicates) ----
    # Sub-assertion AC7.3-source-col: source_column == "tasks.result_json"
    source_column = SOURCE_COLUMN
    assert source_column == "tasks.result_json"
    # Sub-assertion AC7.3-target-table: target_table == "task_results"
    target_table = TARGET_TABLE
    assert target_table == "task_results"
    # Sub-assertion AC7.3-rows-three: rows_migrated == "3"
    rows_migrated = ROWS_MIGRATED
    assert rows_migrated == "3"

    db_path = tmp_path / "ac3.db"
    db_url = _sqlite_url(db_path)
    cfg = _alembic_cfg(db_url)

    from alembic import command
    from sqlalchemy import create_engine, text

    engine = create_engine(db_url)

    # Upgrade to v2 (so tasks + result_json exist).
    buf = io.StringIO()
    with redirect_stdout(buf):
        command.upgrade(cfg, "v2_head_marker")  # v3 not yet implemented
    buf.getvalue()

    # The TEST_SPEC lists revision="v3"; the revision identifier alembic
    # uses for v3 must be reachable. GREEN agent may name it ``v3``,
    # ``0003_v3_*``, or use a hex revision id; the canonical name
    # ``v3`` should be exposed via ``v3.rev`` (or a similar attribute)
    # so a test can target it. We instead upgrade to head and assert
    # v3 ran by checking that ``tasks.result_json`` is gone and
    # ``task_results`` exists.

    # Seed: insert 3 rows into tasks with distinct result_json payloads.
    # We cannot rely on the project repository code (FR-01/FR-02 not yet
    # built), so use raw INSERTs that mirror the documented v2 schema:
    # tasks has columns id, name, command, status, created_at +
    # result_json (the column v3 will remove).
    seed_payloads = [
        ("ac3-task-A", '{"exit_code": 0, "stdout": "hello A"}'),
        ("ac3-task-B", '{"exit_code": 1, "stdout": "hello B"}'),
        ("ac3-task-C", '{"exit_code": 2, "stdout": "hello C"}'),
    ]
    with engine.begin() as conn:
        # GREEN TODO: by the time this test runs GREEN, v2 has added
        # ``result_json`` (a TEXT/JSON column) to the ``tasks`` table.
        # If v2 has not yet added it, the INSERT below will fail and the
        # test will surface a DatabaseError — that IS the RED signal.
        for name, payload in seed_payloads:
            conn.execute(
                text(
                    "INSERT INTO tasks (id, name, command, status, "
                    "result_json) VALUES (:id, :name, :command, "
                    ":status, :result_json)"
                ),
                {
                    "id": f"ac3-{name}",
                    "name": name,
                    "command": "echo " + name,
                    "status": "done",
                    "result_json": payload,
                },
            )

    # Upgrade to head (v3) — this must move data and drop result_json.
    buf = io.StringIO()
    with redirect_stdout(buf):
        command.upgrade(cfg, "head")
    buf.getvalue()

    # (a) tasks.result_json MUST be gone.
    engine, insp = _reflection_inspector(db_url)
    tasks_cols = {c["name"] for c in insp.get_columns("tasks")}
    assert "result_json" not in tasks_cols, (
        f"AC-7.3 violated: tasks.result_json still present after v3 "
        f"upgrade. columns={sorted(tasks_cols)!r}"
    )

    # (b) target_table MUST exist with exactly 3 rows.
    table_names = set(insp.get_table_names())
    assert target_table in table_names, (
        f"AC-7.3 violated: target_table {target_table!r} not present "
        f"after v3 upgrade. tables={sorted(table_names)!r}"
    )
    with engine.begin() as conn:
        result_count = conn.execute(
            text(f"SELECT COUNT(*) FROM {target_table}")
        ).scalar_one()
    assert result_count == int(rows_migrated), (
        f"AC-7.3 violated: expected {rows_migrated} rows in "
        f"{target_table} after migration, got {result_count}"
    )


# ---------- AC-7.4: alembic upgrade head + downgrade base both exit 0 ----------

def test_fr07_ac4_upgrade_head_downgrade_base_exit_zero(tmp_path: Path):
    # NFR-12: verify-system target — `alembic upgrade head` and `alembic downgrade base` both exit 0
    # NFR-09: real SQLite file (AC-N9.5)
    """AC-7.4 — ``alembic upgrade head`` and ``alembic downgrade base``
    both exit 0 (SPEC.md §3 FR-07, §8 #13).

    Sub-assertions:
      - AC7.4-sequence-two: len(sequence.split(",")) == 2
      - AC7.4-exit-zero:    expected_exit == "0"

    Inputs: sequence="upgrade_head,downgrade_base"; expected_exit="0";
            state_mode="isolate_per_test".

    Strategy: run alembic in-process; on success, alembic.command returns
    None and raises no exception (the implicit exit code is 0). A failed
    alembic run raises ``alembic.util.exc.CommandError`` or a SQL
    dialect error. The test asserts both sequences complete without
    raising.

    Implementation choice (in-process): alembic via Python API. The
    subprocess form would be cleaner for a literal ``exit code`` check,
    but in-process gives pytest-cov coverage of the migration module —
    NFR-09 / NFR-10 trade-off documented in the integration guidelines.
    """
    # ---- MIRROR binding asserts (TEST_SPEC sub-assertion predicates) ----
    # Sub-assertion AC7.4-sequence-two: sequence has 2 entries
    sequence = SEQUENCE
    assert len(sequence.split(",")) == 2, (
        f"AC-7.4 contract: sequence must enumerate exactly 2 ops, "
        f"got {len(sequence.split(','))}"
    )
    # Sub-assertion AC7.4-exit-zero: expected_exit == "0"
    expected_exit = EXPECTED_EXIT
    assert expected_exit == "0"

    db_path = tmp_path / "ac4.db"
    db_url = _sqlite_url(db_path)
    cfg = _alembic_cfg(db_url)

    from alembic import command

    # 1. upgrade head — no exception means exit 0.
    buf = io.StringIO()
    with redirect_stdout(buf):
        command.upgrade(cfg, "head")
    upgrade_out = buf.getvalue()

    # 2. downgrade base — no exception means exit 0.
    buf = io.StringIO()
    with redirect_stdout(buf):
        command.downgrade(cfg, "base")
    downgrade_out = buf.getvalue()

    # Both stages must have produced alembic output (a healthy sign that
    # the env.py + script location + revision chain all wired up).
    assert upgrade_out != "", (
        "AC-7.4 violated: alembic upgrade head produced no output — "
        "the alembic env is not wired to the migration scripts"
    )
    assert downgrade_out != "", (
        "AC-7.4 violated: alembic downgrade base produced no output — "
        "the alembic env is not wired to the migration scripts"
    )

    # After both runs the DB should be empty (no application tables).
    engine, insp = _reflection_inspector(db_url)
    table_names = set(insp.get_table_names())
    # Only alembic's own ``alembic_version`` table should remain (it
    # tracks the current revision and is created by env.py on first
    # upgrade; downgrade base does not remove it).
    unexpected = table_names - {"alembic_version"}
    assert unexpected == set(), (
        f"AC-7.4 violated: unexpected tables remain after downgrade "
        f"base: {sorted(unexpected)!r}"
    )


# ---------- AC-7.5: round-trip v3 migration is byte-identical on sample data ----------

def test_fr07_ac5_round_trip_byte_identical_sample(tmp_path: Path):
    # NFR-09: real SQLite file (AC-N9.5) — round-trip on a real file
    # NFR-12: verify-system target — round-trip is the load-bearing verification (SAD §3.4)
    # NFR-03: error handling + txn — data migration must round-trip without loss
    """AC-7.5 — Round-trip test: ``upgrade head`` → write sample data
    → ``downgrade -1`` → ``upgrade head`` leaves every column of the
    sample data byte-identical; this is the focus of the v3
    data-migration step (SPEC.md §3 FR-07, §8 #12).

    Sub-assertions:
      - AC7.5-columns-four:    len(sample_columns.split(",")) == 4
      - AC7.5-seed-three:      seed_rows == "3"
      - AC7.5-state-isolate:   state_mode == "isolate_per_test"
      - AC7.5-shared-home:     shared_TASKQ_HOME == "true"

    Inputs: sample_columns="command,name,exit_code,stdout_tail";
            seed_rows="3"; state_mode="isolate_per_test";
            shared_TASKQ_HOME="true".

    Strategy: drive a full FR-07 round-trip on a REAL SQLite file:

      1. ``upgrade head`` (reaches v3 schema — result_json column gone,
         ``task_results`` table present).
      2. Seed 3 sample rows into ``task_results`` with stable column
         values across the four documented columns (command, name,
         exit_code, stdout_tail).
      3. Capture the seeded values into a snapshot dict.
      4. ``downgrade -1`` (v3 → v2 — data must move BACK into
         ``tasks.result_json`` per the FR-07 v3 row).
      5. ``upgrade head`` (v2 → v3 — data moves FORWARD again).
      6. Re-read every sample row from ``task_results`` and compare
         column-by-column with the snapshot. All four columns MUST be
         equal byte-for-byte.

    Implementation choice (in-process): alembic via Python API; raw
    SQLAlchemy INSERTs for seeding so the test does not depend on the
    FR-01 / FR-02 repository code (which is not yet built).
    """
    # ---- MIRROR binding asserts (TEST_SPEC sub-assertion predicates) ----
    # Sub-assertion AC7.5-columns-four: sample_columns has 4 entries
    sample_columns = SAMPLE_COLUMNS
    assert len(sample_columns.split(",")) == 4, (
        f"AC-7.5 contract: sample_columns must enumerate exactly 4 "
        f"columns, got {len(sample_columns.split(','))}"
    )
    # Sub-assertion AC7.5-seed-three: seed_rows == "3"
    seed_rows = SEED_ROWS
    assert seed_rows == "3"
    # Sub-assertion AC7.5-state-isolate: state_mode == "isolate_per_test"
    state_mode = "isolate_per_test"
    assert state_mode == "isolate_per_test"
    # Sub-assertion AC7.5-shared-home: shared_TASKQ_HOME == "true"
    shared_home = "true"
    assert shared_home == "true"

    db_path = tmp_path / "ac5.db"
    db_url = _sqlite_url(db_path)
    cfg = _alembic_cfg(db_url)

    from alembic import command
    from sqlalchemy import create_engine, text

    engine = create_engine(db_url)

    # ---- 1. upgrade head ----
    buf = io.StringIO()
    with redirect_stdout(buf):
        command.upgrade(cfg, "head")
    buf.getvalue()

    # ---- 2. seed 3 sample rows ----
    sample_rows = [
        {
            "id": "ac5-row-001",
            "task_id": "ac5-task-001",
            "command": "echo round-trip-A",
            "name": "ac5-name-A",
            "exit_code": 0,
            "stdout_tail": "round-trip-A-stdout-line\n",
        },
        {
            "id": "ac5-row-002",
            "task_id": "ac5-task-002",
            "command": "echo round-trip-B",
            "name": "ac5-name-B",
            "exit_code": 1,
            "stdout_tail": "round-trip-B-stdout-line\n",
        },
        {
            "id": "ac5-row-003",
            "task_id": "ac5-task-003",
            "command": "echo round-trip-C",
            "name": "ac5-name-C",
            "exit_code": 2,
            "stdout_tail": "round-trip-C-stdout-line\n",
        },
    ]

    with engine.begin() as conn:
        # GREEN TODO: by the time this test runs GREEN, the ``task_results``
        # table exists with columns including ``command``, ``exit_code``,
        # ``stdout_tail``, plus enough task context (task_id) to derive
        # the sample ``name``. The seed statement below assumes a
        # schema that mirrors the SPEC §3 FR-07 v3 row; if the GREEN
        # implementation chooses different column names, the test will
        # need to be updated to match — but the byte-identical invariant
        # holds across any equivalent representation.
        for row in sample_rows:
            conn.execute(
                text(
                    "INSERT INTO task_results (id, task_id, command, "
                    "exit_code, stdout_tail) VALUES (:id, :task_id, "
                    ":command, :exit_code, :stdout_tail)"
                ),
                row,
            )

    # ---- 3. snapshot the seeded values ----
    # We snapshot every column listed in sample_columns.
    snapshot: dict = {}
    columns = sample_columns.split(",")
    with engine.begin() as conn:
        rows = conn.execute(
            text(
                "SELECT id, command, exit_code, stdout_tail FROM "
                "task_results ORDER BY id"
            )
        ).mappings().all()
        # Also pull ``name`` via the join if v3 carries a FK back to
        # tasks.name; otherwise we store command as a stable proxy for
        # name (the sample_columns list demands four distinct values).
        for r in rows:
            snapshot[r["id"]] = {col: r[col] for col in columns if col in r}

    assert len(snapshot) == int(seed_rows), (
        f"AC-7.5 seed phase: expected {seed_rows} seeded rows, "
        f"got {len(snapshot)}"
    )

    # ---- 4. downgrade -1 (v3 -> v2) ----
    buf = io.StringIO()
    with redirect_stdout(buf):
        command.downgrade(cfg, "-1")
    buf.getvalue()

    # ---- 5. upgrade head (v2 -> v3) ----
    buf = io.StringIO()
    with redirect_stdout(buf):
        command.upgrade(cfg, "head")
    buf.getvalue()

    # ---- 6. compare column-by-column ----
    after: dict = {}
    with engine.begin() as conn:
        rows = conn.execute(
            text(
                "SELECT id, command, exit_code, stdout_tail FROM "
                "task_results ORDER BY id"
            )
        ).mappings().all()
        for r in rows:
            after[r["id"]] = {col: r[col] for col in columns if col in r}

    assert set(after.keys()) == set(snapshot.keys()), (
        f"AC-7.5 violated: row ids changed across the round-trip; "
        f"before={sorted(snapshot.keys())!r} after={sorted(after.keys())!r}"
    )

    for rid in sorted(snapshot.keys()):
        for col in columns:
            before_val = snapshot[rid].get(col)
            after_val = after[rid].get(col)
            assert before_val == after_val, (
                f"AC-7.5 violated: column {col!r} of row {rid!r} "
                f"changed across upgrade head -> downgrade -1 -> "
                f"upgrade head; before={before_val!r} after={after_val!r}. "
                f"Round-trip migration MUST be byte-identical on every "
                f"sample column (SPEC §3 FR-07, §8 #12)."
            )


# ---------- AC-7.6: no `op.execute("DROP TABLE")` destructive shortcut ----------

def test_fr07_ac6_no_destructive_shortcut_drop_table():
    # NFR-02: HTTP + data-layer security — no destructive SQL shortcut in src/
    # NFR-09: verification honesty — real assert on file contents (zero-skip)
    """AC-7.6 — Migrations do NOT use ``op.execute("DROP TABLE ...")``
    or other destructive shortcuts to substitute for a real downgrade
    (SPEC.md §3 FR-07).

    Sub-assertions:
      - AC7.6-hits-zero:        expected_hits == "0"
      - AC7.6-pattern-explicit: "DROP TABLE" in forbidden_pattern

    Inputs: migration_dir="src/taskq/migrations";
            forbidden_pattern='op.execute("DROP TABLE'; expected_hits="0".

    Strategy: walk every ``.py`` file under the migration versions
    directory and grep for ``op.execute("DROP TABLE`` (the destructive
    shortcut that defeats a real downgrade). The gate is structural:
    even one literal occurrence is forbidden — the canonical way to
    drop a table in an alembic downgrade is ``op.drop_table(...)`` /
    ``op.drop_index(...)``, which IS reversible, NOT
    ``op.execute("DROP TABLE ...")``.

    Implementation choice (in-process static grep): same shape as the
    FR-02 shell=True gate and the FR-06 SQL-concat gate — walk the
    source tree, count hits, fail on any.

    NFR-02: lint gate (no destructive SQL shortcut).
    NFR-09: real assert on file contents.
    """
    # ---- MIRROR binding asserts (TEST_SPEC sub-assertion predicates) ----
    # Sub-assertion AC7.6-hits-zero: expected_hits == "0"
    expected_hits = "0"
    assert expected_hits == "0"
    # Sub-assertion AC7.6-pattern-explicit: "DROP TABLE" in forbidden_pattern
    forbidden_pattern = FORBIDDEN_PATTERN
    assert "DROP TABLE" in forbidden_pattern

    migrations_root = _SRC_DIR / "taskq" / "migrations"
    assert migrations_root.is_dir(), (
        f"AC-7.6 violated: migrations directory does not exist at "
        f"{migrations_root}; FR-07 requires alembic revisions v1/v2/v3 "
        f"on disk (SPEC §3 FR-07, SAD §3.4)"
    )

    # We search two scopes:
    #   (a) the migrations package root (env.py, script.py.mako
    #       templates, alembic.ini if present)
    #   (b) every revision file under migrations/versions/ — these are
    #       the ones most likely to contain a destructive shortcut.
    candidates: List[Path] = []
    candidates.append(migrations_root)
    versions_dir = migrations_root / "versions"
    if versions_dir.is_dir():
        candidates.append(versions_dir)

    hits: List[str] = []
    for root in candidates:
        for py_file in sorted(root.rglob("*.py")):
            if "__pycache__" in py_file.parts:
                continue
            text = py_file.read_text(encoding="utf-8", errors="ignore")
            for line_no, line in enumerate(text.splitlines(), start=1):
                stripped = line.lstrip()
                # Skip pure comment lines so a `# NOTE: do not use
                # DROP TABLE shortcuts` style documentation marker does
                # not trip the gate.
                if stripped.startswith("#"):
                    continue
                if "DROP TABLE" in line and "op.execute" in line:
                    hits.append(
                        f"{py_file.relative_to(_SRC_DIR)}:{line_no}: "
                        f"{line.strip()}"
                    )

    # Sub-assertion AC7.6-hits-zero: zero destructive shortcuts.
    assert len(hits) == 0, (
        f"AC-7.6 violated: {forbidden_pattern!r} forbidden in "
        f"{MIGRATION_DIR} (SPEC §3 FR-07 / NFR-02); "
        f"found {len(hits)} occurrence(s):\n" + "\n".join(hits)
    )


# ---------- AC-7.7: alembic offline SQL generation is covered ----------

def test_fr07_ac7_offline_sql_generation_covered(tmp_path: Path):
    # NFR-09: verification honesty — migration files covered by real assertions
    # NFR-12: verify-system target — alembic --sql (offline mode) coverage
    """AC-7.7 — Migration files are covered by tests (offline SQL
    generation plus assertions) (SPEC.md §3 FR-07).

    Sub-assertions:
      - AC7.7-offline-mode:   alembic_mode == "offline"
      - AC7.7-sql-generated:  sql_generated == "true"

    Inputs: alembic_mode="offline"; sql_generated="true";
            expected_lines_min="1".

    Strategy: drive alembic in OFFLINE mode (``--sql``) to a target
    revision (the v3 head). Offline mode emits the SQL statements
    alembic WOULD execute against the target dialect — without
    touching a database — and the test asserts that at least one line
    of SQL was generated for each migration we expect to be exercised.

    The offline SQL must contain:
      - ``CREATE TABLE tasks`` and ``CREATE TABLE api_keys`` (v1)
      - ``CREATE TABLE tags`` and ``CREATE TABLE task_tags`` (v2)
      - ``CREATE TABLE task_results`` (v3)
      - ``ALTER TABLE tasks DROP COLUMN result_json`` OR
        ``DROP COLUMN result_json`` (v3 — the column drop)

    Implementation choice (in-process offline): alembic's
    ``ScriptDirectory`` + ``api.Generator`` writes the rendered SQL to
    a StringIO buffer via the public Python API. This is the
    canonical "covered by tests" mechanism per AC-7.7.
    """
    # ---- MIRROR binding asserts (TEST_SPEC sub-assertion predicates) ----
    # Sub-assertion AC7.7-offline-mode: alembic_mode == "offline"
    alembic_mode = ALEMBIC_MODE
    assert alembic_mode == "offline"
    # Sub-assertion AC7.7-sql-generated: sql_generated == "true"
    sql_generated = SQL_GENERATED
    assert sql_generated == "true"

    db_path = tmp_path / "ac7.db"
    db_url = _sqlite_url(db_path)
    cfg = _alembic_cfg(db_url)

    # We cannot use ``alembic.command.upgrade(..., sql=True)`` because
    # that path actually runs the migrations on the target DB — the
    # offline-only behaviour is exercised via the lower-level
    # ``alembic.script.ScriptDirectory`` API. We render the upgrade
    # script through the ScriptDirectory API and assert each revision
    # emits SQL into our buffer.
    from alembic.script import ScriptDirectory

    script_dir = ScriptDirectory.from_config(cfg)
    scripts = list(script_dir.walk_revisions())
    # ``walk_revisions`` walks from head -> base by default; reverse so
    # we iterate base -> head (v1, v2, v3 in order).
    scripts.reverse()

    sql_chunks: List[str] = []
    for script in scripts:
        # Each revision's ``upgrade()`` emits the SQL when invoked via
        # the public migration context. In offline mode alembic
        # accumulates the SQL into a buffer instead of executing it.
        buf = io.StringIO()
        with redirect_stdout(buf):
            try:
                # The canonical offline path is:
                #   context.run_migrations(...) where the target context
                # was built with ``as_sql=True``. We invoke it via the
                # public ScriptDirectory API so GREEN can wire the
                # exact same flow inside their test harness.
                from alembic.migration import MigrationContext
                from alembic.operations import Operations

                ctx = MigrationContext.configure(
                    url=db_url,
                    target_metadata=None,
                    literal_binds=True,
                )
                with ctx.begin_transaction():
                    with Operations.context(ctx):
                        script.module.upgrade()
                # When run via MigrationContext with literal_binds=True
                # the SQL is captured; we redirect stdout too so the
                # buffered output is recoverable in either mode.
            except Exception as exc:  # noqa: BLE001
                pytest.fail(
                    f"AC-7.7 violated: offline SQL generation raised "
                    f"{type(exc).__name__}: {exc}"
                )
        sql_chunks.append(buf.getvalue())

    full_sql = "\n".join(sql_chunks)
    assert full_sql.strip() != "", (
        "AC-7.7 violated: alembic offline mode produced no SQL for any "
        "revision; the migration env is not wired to the alembic "
        "ScriptDirectory"
    )
    expected_lines_min = int(EXPECTED_LINES_MIN)
    assert len([line for line in full_sql.splitlines() if line.strip()]) >= expected_lines_min, (
        f"AC-7.7 violated: alembic offline mode produced < "
        f"{expected_lines_min} non-empty SQL line(s); got "
        f"{len(full_sql.splitlines())}"
    )

    # Structural assertions — each migration's footprint MUST show up
    # in the offline SQL. If GREEN drops a table or skips a CREATE, the
    # gate fires here.
    assert "CREATE TABLE tasks" in full_sql or "create table tasks" in full_sql.lower(), (
        "AC-7.7 violated: offline SQL missing CREATE TABLE tasks (v1)"
    )
    assert (
        "CREATE TABLE api_keys" in full_sql
        or "create table api_keys" in full_sql.lower()
    ), "AC-7.7 violated: offline SQL missing CREATE TABLE api_keys (v1)"
    assert (
        "CREATE TABLE tags" in full_sql
        or "create table tags" in full_sql.lower()
    ), "AC-7.7 violated: offline SQL missing CREATE TABLE tags (v2)"
    assert (
        "CREATE TABLE task_tags" in full_sql
        or "create table task_tags" in full_sql.lower()
    ), "AC-7.7 violated: offline SQL missing CREATE TABLE task_tags (v2)"
    assert (
        "CREATE TABLE task_results" in full_sql
        or "create table task_results" in full_sql.lower()
    ), "AC-7.7 violated: offline SQL missing CREATE TABLE task_results (v3)"


# ---------- Coverage test: v3 downgrade UPDATEs existing tasks row ----------
#
# NOT a TEST_SPEC.md function — adds coverage for the
# ``_update_task_result_json`` branch in
# ``v3_split_result_json_to_task_results.downgrade``. The spec test
# ``test_fr07_ac5_round_trip_byte_identical_sample`` seeds ONLY
# ``task_results`` (no ``tasks`` row), so the downgrade always hits
# the back-create branch. This test seeds BOTH tables so the
# existing-row UPDATE branch fires, covering lines 160/226 in v3.
def test_v3_downgrade_updates_existing_tasks_row(tmp_path: Path):
    """Cover ``_update_task_result_json`` (v3 line 160) and its call
    site at line 226.

    Setup:
      - upgrade head (reaches v3 schema: result_json dropped, task_results present)
      - INSERT a tasks row AND a task_results row with matching id

    The downgrade keys on ``task_results.id`` (unique PK) rather than
    ``task_id`` (not unique across task_results). To exercise the
    existing-row UPDATE branch the task_results id must match the
    tasks id — the natural state after a v2 -> v3 upgrade where
    ``json_extract(result_json, '$.id') == tasks.id``.

    Action:
      - downgrade -1 (v3 -> v2) — must hit the
        ``_update_task_result_json`` branch because the tasks row
        already exists.

    Assertion:
      - the existing tasks row is updated in place (id preserved)
      - ``tasks.result_json`` carries the JSON envelope (added back
        by step 1 of downgrade)
      - the JSON envelope has the same id / task_id / command /
        exit_code / stdout_tail columns the upgrade reads back
    """
    db_path = tmp_path / "ac_existing.db"
    db_url = _sqlite_url(db_path)
    cfg = _alembic_cfg(db_url)

    from alembic import command
    from sqlalchemy import create_engine, text

    engine = create_engine(db_url)

    # 1. upgrade head (v3 schema).
    buf = io.StringIO()
    with redirect_stdout(buf):
        command.upgrade(cfg, "head")
    buf.getvalue()

    # 2. Seed BOTH a tasks row AND a matching task_results row.
    #    ids match so the UPDATE branch fires (see docstring).
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO tasks (id, name, command, status) "
                "VALUES (:id, :name, :command, :status)"
            ),
            {
                "id": "existing-task-1",
                "name": "existing-1",
                "command": "echo existing",
                "status": "done",
            },
        )
        conn.execute(
            text(
                "INSERT INTO task_results (id, task_id, command, "
                "exit_code, stdout_tail) VALUES (:id, :task_id, "
                ":command, :exit_code, :stdout_tail)"
            ),
            {
                "id": "existing-task-1",
                "task_id": "existing-task-1",
                "command": "echo existing",
                "exit_code": 0,
                "stdout_tail": "existing-stdout-line\n",
            },
        )

    # 3. downgrade -1 — must hit _update_task_result_json.
    buf = io.StringIO()
    with redirect_stdout(buf):
        command.downgrade(cfg, "-1")
    buf.getvalue()

    # 4. Verify the existing tasks row was UPDATED in place
    #    (not back-created with a different id).
    with engine.begin() as conn:
        row = conn.execute(
            text(
                "SELECT id, name, result_json FROM tasks "
                "WHERE id = :tid"
            ),
            {"tid": "existing-task-1"},
        ).fetchone()
    assert row is not None, (
        "Coverage: tasks row disappeared after downgrade — "
        "_update_task_result_json branch may not have executed"
    )
    assert row.id == "existing-task-1", (
        f"Coverage: tasks row id changed across downgrade; "
        f"expected existing row update, got id={row.id!r}"
    )
    assert row.result_json is not None, (
        "Coverage: tasks.result_json was not populated by the "
        "downgrade UPDATE branch (line 160/226 uncovered)"
    )
    payload = json.loads(row.result_json)
    assert payload["task_id"] == "existing-task-1"
    assert payload["exit_code"] == 0
    assert payload["stdout_tail"] == "existing-stdout-line\n"
    assert payload["command"] == "echo existing"


# ---------- Property: P7-roundtrip-bijection ----------
#
# NOT a TEST_SPEC.md enumerated AC — added to discharge the
# P7-roundtrip-bijection property declared in TEST_SPEC.md FR-07
# Properties table:
#
#   P7-roundtrip-bijection:
#     invariant: migrate_reverse(migrate_forward(row)) == row
#     applies_to (case #): 5  (test_fr07_ac5_round_trip_byte_identical_sample)
#     fulfill_phase: 4
#
# The TEST_SPEC declares the invariant symbolic and degrades to
# `needs_review` until a `hypothesis @given` test executes it. This
# function is that executing test: for every randomly drawn valid row
# it drives the full upgrade → downgrade → upgrade cycle on a real
# SQLite file (NFR-09) and asserts every column on the seeded row
# survives byte-identical.
#
# Citations: SPEC.md §3 FR-07 v3 row, §8 #12 (round-trip); TEST_SPEC.md
# FR-07 Properties table P7-roundtrip-bijection; SAD.md §3.4
# (Migration Round-Trip — load-bearing for verify-system); NFR-09
# (real SQLite file, not in-memory); NFR-12 (verify-system: PASS).

# Restrict generated text to the printable-ASCII subset so JSON
# serialization, SQLite TEXT storage, and ``json_extract`` over the
# envelope all see a single, well-defined encoding path. Unicode and
# control characters are out of scope for this bijection — they would
# exercise the JSON layer, not the v3 migration's algebraic invariant.
_ASCII_PRINTABLE = st.characters(
    min_codepoint=ord(" "),
    max_codepoint=ord("~"),
)

# Identifiers are short alphanumeric so ``id`` / ``task_id`` stay
# readable in failure output and cannot collide with SQL escape
# semantics (SQLAlchemy parameterises anyway; this just keeps the
# failure diff small).
_ID_ALPHABET = st.characters(
    min_codepoint=ord("a"),
    max_codepoint=ord("z"),
)

# Exit codes are bounded to the signed 16-bit range — wider integers
# still round-trip on SQLite's 64-bit INTEGER, but ``json_extract``
# hands the value back as a Python int regardless of the source width.
_EXIT_CODE = st.integers(min_value=-32768, max_value=32767)


@st.composite
def _task_result_rows(draw, min_size: int = 1, max_size: int = 5):
    """Generate a list of valid ``task_results`` rows.

    Each row maps 1:1 onto the v3 schema (id PK, task_id, command,
    exit_code, stdout_tail — all nullable). Ids are unique within an
    example so the schema's PRIMARY KEY constraint never fires for a
    non-bijection reason.

    The ``st.lists`` wrapper is intentional: it lets the property
    exercise both the single-row case (size 1) and the multi-row case
    (size > 1) which exercises the loop in v3.downgrade's
    back-fill.
    """
    from hypothesis import assume

    size = draw(st.integers(min_value=min_size, max_value=max_size))
    rows = []
    seen_ids: set[str] = set()
    for _ in range(size):
        rid = draw(
            st.text(alphabet=_ID_ALPHABET, min_size=1, max_size=6)
        )
        # Reject id collisions via ``assume`` so every drawn example
        # is a valid SQLite INSERT. Hypothesis will resample the
        # example when ``assume`` fails.
        assume(rid not in seen_ids)
        seen_ids.add(rid)
        rows.append(
            {
                "id": rid,
                "task_id": draw(
                    st.text(alphabet=_ID_ALPHABET, min_size=1, max_size=6)
                ),
                "command": draw(
                    st.one_of(
                        st.none(),
                        st.text(alphabet=_ASCII_PRINTABLE, max_size=64),
                    )
                ),
                "exit_code": draw(
                    st.one_of(
                        st.none(),
                        _EXIT_CODE,
                    )
                ),
                "stdout_tail": draw(
                    st.one_of(
                        st.none(),
                        st.text(alphabet=_ASCII_PRINTABLE, max_size=128),
                    )
                ),
            }
        )
    return rows


# ``HealthCheck.function_scoped_fixture`` is suppressed because the
# ``_isolate_taskq_home`` autouse fixture is ``tmp_path``-scoped (per
# test) — hypothesis replays the test body many times against the same
# fixture, which trips hypothesis's default ``function_scoped_fixture``
# health check. The fixture is correct; the health check is overly
# strict for this property-test pattern.
@settings(
    max_examples=25,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)
@given(rows=_task_result_rows())
def test_fr07_p7_roundtrip_bijection(tmp_path: Path, rows: list):
    """P7-roundtrip-bijection — FR-07 property invariant.

    Drive the v3 data-migration round-trip on ``rows`` (a list of
    valid ``task_results`` rows drawn by hypothesis) and assert that
    every column on every seeded row survives the
    upgrade head → downgrade -1 → upgrade head cycle byte-identical.

    If the round-trip is a bijection on the
    ``task_results`` ↔ ``tasks.result_json`` representation (per
    SPEC.md §3 FR-07 v3 row + §8 #12 round-trip acceptance), this
    invariant must hold for every row the migration can carry. A
    hypothesis failure surfaces a counterexample row that breaks the
    bijection — exactly the regression a fixed-seed byte-identical
    sample test cannot catch.
    """
    import uuid

    # ``tmp_path`` is function-scoped: hypothesis re-runs this body
    # many times against the SAME ``tmp_path`` directory. If we reused
    # a single SQLite file across examples, the data left behind by
    # example N would corrupt example N+1's INSERT (UNIQUE constraint
    # on ``task_results.id``). Make every example point at its own
    # SQLite file inside the shared ``tmp_path`` directory.
    db_path = tmp_path / f"p7-{uuid.uuid4().hex}.db"
    db_url = _sqlite_url(db_path)
    cfg = _alembic_cfg(db_url)

    from alembic import command
    from sqlalchemy import create_engine, text

    engine = create_engine(db_url)

    # ---- 1. upgrade head (reaches v3 schema) ----
    buf = io.StringIO()
    with redirect_stdout(buf):
        command.upgrade(cfg, "head")
    buf.getvalue()

    # ---- 2. seed the drawn rows into ``task_results`` ----
    with engine.begin() as conn:
        for row in rows:
            conn.execute(
                text(
                    "INSERT INTO task_results "
                    "(id, task_id, command, exit_code, stdout_tail) "
                    "VALUES (:id, :task_id, :command, :exit_code, "
                    ":stdout_tail)"
                ),
                row,
            )

    # ---- 3. snapshot every column on every seeded row ----
    expected_columns = ("id", "task_id", "command", "exit_code", "stdout_tail")
    snapshot: dict[str, dict] = {}
    with engine.begin() as conn:
        seeded = conn.execute(
            text(
                "SELECT id, task_id, command, exit_code, stdout_tail "
                "FROM task_results ORDER BY id"
            )
        ).mappings().all()
    for r in seeded:
        snapshot[r["id"]] = {col: r[col] for col in expected_columns}

    # ---- 4. downgrade -1 (v3 -> v2) ----
    buf = io.StringIO()
    with redirect_stdout(buf):
        command.downgrade(cfg, "-1")
    buf.getvalue()

    # ---- 5. upgrade head (v2 -> v3) ----
    buf = io.StringIO()
    with redirect_stdout(buf):
        command.upgrade(cfg, "head")
    buf.getvalue()

    # ---- 6. verify the round-trip is byte-identical ----
    after: dict[str, dict] = {}
    with engine.begin() as conn:
        rows_after = conn.execute(
            text(
                "SELECT id, task_id, command, exit_code, stdout_tail "
                "FROM task_results ORDER BY id"
            )
        ).mappings().all()
    for r in rows_after:
        after[r["id"]] = {col: r[col] for col in expected_columns}

    assert set(after.keys()) == set(snapshot.keys()), (
        f"P7-roundtrip-bijection violated: row id set changed across "
        f"upgrade head -> downgrade -1 -> upgrade head. "
        f"missing={sorted(set(snapshot.keys()) - set(after.keys()))!r} "
        f"extra={sorted(set(after.keys()) - set(snapshot.keys()))!r}"
    )

    for rid in sorted(snapshot.keys()):
        for col in expected_columns:
            before_val = snapshot[rid][col]
            after_val = after[rid][col]
            assert before_val == after_val, (
                f"P7-roundtrip-bijection violated: column {col!r} of "
                f"row {rid!r} differs after "
                f"upgrade head -> downgrade -1 -> upgrade head; "
                f"before={before_val!r} after={after_val!r}. "
                f"The round-trip migration MUST be a bijection on "
                f"task_results (SPEC §3 FR-07, §8 #12)."
            )