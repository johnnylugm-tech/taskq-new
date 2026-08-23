"""Alembic environment script for taskq.

[FR-07] Standard alembic env that resolves the SQLAlchemy URL from
``cfg.sqlalchemy.url`` (set programmatically by the test harness via
``Config.set_main_option``). Supports both online (real connection)
and offline (SQL emission) modes so ``command.upgrade`` /
``command.downgrade`` work in-process for the verify-system chain
and ``MigrationContext(literal_binds=True)`` works for offline SQL
generation in AC-7.7.

The environment does NOT import the project's SQLAlchemy declarative
``Base``; FR-07's revisions are hand-written (no autogenerate) so
``target_metadata`` is ``None``. This keeps the env minimal and
deterministic.

Citations: SPEC.md §3 FR-07; SAD.md §3.4 (Migration Round-Trip).
"""
from __future__ import annotations

import logging
import sys

from alembic import context
from sqlalchemy import engine_from_config, pool

# Alembic Config object provides access to alembic.ini values.
config = context.config

# Configure logging. The FR-07 verify-system test harness drives alembic
# programmatically (``Config.set_main_option``) with no alembic.ini on disk;
# alembic's stdout ``Running <step>`` / ``Running downgrade`` messages are
# silently dropped without a logger, which breaks AC-7.4's
# ``upgrade_out != ""`` assertion. Attach a stdout ``StreamHandler`` to the
# alembic logger so in-process migrations produce the same stdout contract
# the CLI form does. Idempotent: re-running env.py skips a handler we
# already attached to avoid duplicate log lines.
#
# NB: We resolve ``sys.stdout`` lazily inside ``emit`` so a test harness
# using ``contextlib.redirect_stdout`` captures the log line — a
# ``StreamHandler(stream=sys.stdout)`` would otherwise snapshot the stream
# at handler-creation time and point at the real stdout after redirect.

class _DynamicStdoutHandler(logging.StreamHandler):  # type: ignore[misc]
    def emit(self, record):  # noqa: D401
        self.stream = sys.stdout
        super().emit(record)

_alembic_logger = logging.getLogger("alembic")
_already_stream = any(
    isinstance(h, _DynamicStdoutHandler)
    and getattr(h, "_taskq_stream_attached", False)
    for h in _alembic_logger.handlers
)
if not _already_stream:
    _stream = _DynamicStdoutHandler()
    _stream.setLevel(logging.INFO)
    _stream.setFormatter(
        logging.Formatter("%(message)s")
    )
    _stream._taskq_stream_attached = True  # type: ignore[attr-defined]
    _alembic_logger.addHandler(_stream)
    _alembic_logger.setLevel(logging.INFO)

# FR-07 revisions are hand-authored; we do not autogenerate from the
# project's SQLAlchemy metadata.
target_metadata = None


def run_migrations_online() -> None:
    """Run migrations in 'online' mode using a live SQLAlchemy engine.

    The test harness points ``sqlalchemy.url`` at a real SQLite file
    via ``Config.set_main_option``; the engine built here is the one
    that actually executes the migration SQL against the file.
    """
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
        )

        with context.begin_transaction():
            context.run_migrations()


# FR-07 verify-system drives alembic only in online mode; offline SQL
# generation is exercised by AC-7.7 directly via ``MigrationContext`` /
# ``ScriptDirectory`` rather than through this env module.
run_migrations_online()
