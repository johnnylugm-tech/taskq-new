"""taskq.migrations — Alembic migration package for taskq.

[FR-07] FR-07 schema migration package root. Exposes the alembic env,
script location template, and three revisions (v1, v2, v3) under
``versions/``.

Alembic's ``ScriptDirectory`` discovers the revision files via the
``versions/`` sub-package; the test harness drives alembic in-process
(via ``command.upgrade`` / ``command.downgrade``) using
``script_location = ```` ``<src>/taskq/migrations`` ```` so the env,
template, and revision files are wired together without an
``alembic.ini`` file on disk.

Citations: SPEC.md §3 FR-07; SAD.md §3.4.
"""
from __future__ import annotations
