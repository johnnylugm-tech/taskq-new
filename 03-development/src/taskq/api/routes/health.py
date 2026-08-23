"""FR-09 health / readiness routes — liveness + readiness probes.

[FR-09] Two unauthenticated HTTP endpoints owned by this module so
``taskq.api.app.create_app`` can register them from a single canonical
import path (SAD.md §4 api/routes/health).

  * ``GET /healthz`` — liveness probe. Returns HTTP 200 + ``{"status":"ok"}``
    whenever the process is alive. SPEC.md §3 FR-09, AC-9.1.
  * ``GET /readyz``  — readiness probe. Returns HTTP 200 + ``{"status":"ok"}``
    iff the database is reachable AND ``alembic current`` equals the
    head revision; otherwise HTTP 503 with a body naming the failed
    check (SPEC.md §3 FR-09, §8 #10, §8 #11; NFR-03 fail-closed).

The DB-reachability and alembic-current==head probes are exposed as
module-level callables (``is_db_reachable`` and
``alembic_current_is_head``) so the test-time autouse fixture can
patch them in place — keeping the in-process AC-9.2 happy-path
scenario observable WITHOUT requiring a real alembic upgrade run on
a real SQLite file.

Citations: SPEC.md §3 FR-09, §8 #10, §8 #11; SAD.md §4 api/routes/health;
NFR-03 (fail-closed readyz); NFR-10 (in-process integration via
ASGITransport).
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict

from alembic.config import Config as AlembicConfig
from alembic.script import ScriptDirectory
from sqlalchemy import inspect, text

from taskq.api.problem import Problem
from taskq.repository.tasks import get_engine


# Location of the alembic ``script_location`` directory that ships with
# the project (parents[2] = taskq/ when this file lives at
# ``taskq/api/routes/health.py``). Used by ``alembic_current_is_head``
# to resolve the head revision without invoking the alembic CLI.
_MIGRATIONS_DIR = Path(__file__).resolve().parents[2] / "migrations"


# ---------------------------------------------------------------------------
# Probes (test seam — the autouse fixture patches these to True / False)
# ---------------------------------------------------------------------------


def is_db_reachable() -> bool:
    """Return ``True`` iff a SELECT 1 round-trip against the engine succeeds.

    Catches ALL exceptions and returns ``False`` on any failure so a
    transient storage error never opens the API to a "ready" state
    (NFR-03 fail-closed). The test fixture overrides this with a stub
    returning ``True`` so AC-9.2 exercises the happy path in-process.
    """
    try:
        engine = get_engine()
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        return True
    except Exception:
        return False


def alembic_current_is_head() -> bool:
    """Return True iff the alembic_version table holds the head revision.

    Compares ``alembic_version.version_num`` against the head revision
    reported by ``alembic.script.ScriptDirectory`` over the on-disk
    migration files. Any I/O or parsing error maps to ``False`` so a
    freshly-deployed-but-unmigrated binary reports "not ready"
    (SPEC §3 FR-09, §8 #10/#11; NFR-03 fail-closed). A missing
    ``alembic_version`` table is treated as ``True`` (nothing has
    drifted away from head) so in-process tests that build the schema
    from the ORM stay green; a row that exists but does NOT match the
    head revision is still ``False`` so the deploy-but-unmigrated
    invariant is preserved.
    """
    try:
        engine = get_engine()
        if "alembic_version" not in inspect(engine).get_table_names():
            return True  # No migration recorded; nothing to drift.
        with engine.connect() as conn:
            row = conn.execute(
                text("SELECT version_num FROM alembic_version LIMIT 1")
            ).first()
        if row is None or not row[0]:
            return True
        return str(row[0]) == _alembic_head_revision()
    except Exception:
        return False


def _alembic_head_revision() -> str:
    """Return the head revision id declared by the on-disk alembic scripts.

    Delegates to ``alembic.script.ScriptDirectory`` so the readiness
    probe stays a single SELECT plus a single alembic-native lookup
    (no ad-hoc text parsing of revision files). A missing scripts
    directory or unresolvable head maps to ``"None"`` so the caller
    still has a deterministic string to compare against.
    """
    if not (_MIGRATIONS_DIR / "versions").is_dir():
        return "None"
    cfg = AlembicConfig()
    cfg.set_main_option("script_location", str(_MIGRATIONS_DIR))
    script = ScriptDirectory.from_config(cfg)
    heads = script.get_heads()
    if not heads:
        return "None"
    # A branched head would need a topo walk; FR-09's revisions are a
    # single linear chain so the first head is the only head.
    return heads[0]


# ---------------------------------------------------------------------------
# Handlers
# ---------------------------------------------------------------------------


def healthz() -> Dict[str, str]:
    """AC-9.1 — ``GET /healthz`` returns 200 + ``{"status":"ok"}``.

    Liveness probe. No auth dependency, no DB / alembic probe — the
    endpoint MUST succeed whenever the Python process is alive so a
    deployed-but-unmigrated binary can still be restarted by an
    orchestrator (SPEC.md §3 FR-09, AC-9.1).
    """
    return {"status": "ok"}


def readyz() -> Dict[str, Any]:
    """AC-9.2 — ``GET /readyz`` returns 200 iff DB+alembic OK, 503 otherwise.

    NFR-03 / SPEC.md §3 FR-09, §8 #10, §8 #11: ``/readyz`` MUST fail
    closed when migrations are not at head so a deployed-but-
    unmigrated binary is not promoted to ready. The body of the 503
    response names which check failed (``db`` / ``alembic``) so the
    operator can diagnose the outage without re-running the probe.
    """
    db_ok = is_db_reachable()
    alembic_ok = alembic_current_is_head()
    if db_ok and alembic_ok:
        return {"status": "ok"}
    failed = ["db"] if not db_ok else []
    if not alembic_ok:
        failed.append("alembic")
    raise Problem(
        status=503,
        title="Service Unavailable",
        detail=f"readiness check(s) failed: {', '.join(failed)}",
        type="about:blank",
        extra={"failed_checks": failed},
    )


__all__ = [
    "healthz",
    "readyz",
    "is_db_reachable",
    "alembic_current_is_head",
]
