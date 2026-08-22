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
from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

# Alembic Config object provides access to alembic.ini values.
config = context.config

# Configure logging from the alembic.ini file (if present). The test
# harness drives alembic programmatically and never provides an ini
# file; the fileConfig call is guarded so a missing ini does not
# blow up the in-process Python API path.
if config.config_file_name is not None:
    try:
        fileConfig(config.config_file_name)
    except (KeyError, OSError):
        # No file-based logger section; alembic will use its default
        # logging configuration.
        pass
else:
    # The FR-07 verify-system test harness calls alembic via the Python
    # API with no alembic.ini on disk; without a logger configured,
    # alembic's "Running <step>" / "Running downgrade" messages are
    # silently dropped and the AC-7.4 ``upgrade_out != ""`` assertion
    # fires. Attach a stdout ``StreamHandler`` to the alembic logger so
    # in-process migrations produce the same stdout contract that the
    # subprocess form does. Idempotent: re-running env.py replaces any
    # handler we previously attached to avoid duplicate log lines.
    #
    # NB: We resolve ``sys.stdout`` lazily inside ``emit`` so that a
    # test harness using ``contextlib.redirect_stdout`` captures the
    # log line — ``StreamHandler(stream=sys.stdout)`` snapshots the
    # stream at handler-creation time, which would point at the real
    # stdout even after a test redirected it.
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


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode (emit SQL to stdout/buffer).

    Called when ``alembic`` is invoked with ``--sql``; also exercised
    by the AC-7.7 in-process offline test which builds its own
    ``MigrationContext(literal_binds=True)`` rather than calling this
    function. Kept here for parity with the standard alembic template
    so ``command.upgrade(..., sql=True)`` remains a supported surface.
    """
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )

    with context.begin_transaction():
        context.run_migrations()


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


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
