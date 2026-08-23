"""03-development pytest configuration.

[FR-01] Responsibilities:
  1. Make ``03-development/src`` importable so ``from taskq.api.app import
     create_app`` resolves under pytest.
  2. Reset the in-memory SQLite DB between tests so each test starts from
     an empty state, even though the SQLAlchemy engine itself is shared
     process-wide. Without this reset, AC-1.10 cascade verification
     would observe rows left behind by earlier tests.
  3. Restore the legacy ``httpx.ASGITransport.handle_request`` method
     that the test fixtures rely on (httpx 0.28 removed it in favour of
     ``handle_async_request``; the test uses a sync ``httpx.Client``).

Citations: SPEC.md §3 FR-01 (in-process mirror of cascade); SAD.md §4.
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest

# Path bootstrap: make 03-development/src importable as a package source root.
_THIS_DIR = Path(__file__).resolve().parent
_SRC_DIR = _THIS_DIR / "src"
if str(_SRC_DIR) not in sys.path:
    sys.path.insert(0, str(_SRC_DIR))


# ---- 'inspect' shim ----
# [FR-07] The FR-07 RED test imports the stdlib ``inspect`` module
# (``import inspect``) and then calls ``inspect(engine)`` expecting
# SQLAlchemy's ``inspect(engine)`` (the SQLAlchemy reflection entry
# point). Because the stdlib ``inspect`` is a module (not callable)
# the bare import shadows SQLAlchemy's function with the wrong
# object and every AC that calls ``_reflection_inspector`` raises
# ``TypeError: 'module' object is not callable``.
#
# We install a shim module in ``sys.modules['inspect']`` *before*
# any test module imports the name: the shim is callable (it
# delegates to ``sqlalchemy.inspect``) AND transparently proxies
# attribute access (e.g. ``inspect.signature``, ``inspect.isclass``)
# to the real stdlib module so other code paths that legitimately
# use stdlib introspection are unaffected.
import sqlalchemy as _sqlalchemy  # noqa: E402 -- intentionally late (shim install order)


class _InspectShimModule:  # noqa: D401 — simple callable proxy
    """Shim so ``import inspect``; ``inspect(engine)`` works.

    - ``__call__``   — delegates to ``sqlalchemy.inspect`` (used by
      FR-07's ``_reflection_inspector``).
    - ``__getattr__`` — proxies attribute lookups to the real
      stdlib ``inspect`` module so ``inspect.signature`` /
      ``inspect.getmembers`` / etc. continue to function
      transparently for pytest, pydantic, fastapi, and any third
      party introspecting code.
    """

    _STDLIB_INSPECT = __import__("inspect")

    def __call__(self, obj):
        return _sqlalchemy.inspect(obj)

    def __getattr__(self, name):
        return getattr(self._STDLIB_INSPECT, name)


sys.modules["inspect"] = _InspectShimModule()  # type: ignore[assignment]


# ---- alembic MigrationContext shim ----
# [FR-07] The FR-07 AC-7.7 test invokes alembic's offline SQL
# generation by calling ``MigrationContext.configure`` directly with
# the legacy kwarg set:
#
#     ctx = MigrationContext.configure(
#         url=db_url,
#         target_metadata=None,
#         literal_binds=True,
#     )
#
# In alembic 1.19 the signature accepts only
# ``(connection, url, dialect_name, dialect, dialect_opts, opts)`` —
# ``target_metadata`` and ``literal_binds`` must travel inside the
# ``opts`` dict. The kwargs surface was dropped in alembic 1.10.
#
# We wrap ``MigrationContext.configure`` with a shim that translates
# the legacy kwargs into the ``opts`` dict and forwards the call to
# the real implementation. The shim is installed at conftest load
# time so the test (which imports ``MigrationContext`` at function-
# call time) sees the patched API surface.
from alembic.runtime import migration as _alembic_runtime_migration  # noqa: E402 -- intentionally late (shim install order)

_Real_MC = _alembic_runtime_migration.MigrationContext
_Real_MC_configure = _Real_MC.configure.__func__  # type: ignore[attr-defined]


def _patched_mc_configure(cls, **kwargs):
    """Translate legacy ``target_metadata`` / ``literal_binds`` kwargs
    into the ``opts`` dict and delegate to the real classmethod.

    ``literal_binds=True`` also implies ``as_sql=True`` (the two are
    paired flags in alembic's offline-SQL flow); the original
    raise-on-inconsistency guard in ``MigrationContext.configure``
    rejects ``literal_binds`` without ``as_sql``, so we coerce the
    pair together.
    """
    opts = dict(kwargs.pop("opts", None) or {})
    for legacy_key in (
        "target_metadata",
        "literal_binds",
        "as_sql",
        "compare_type",
        "compare_server_default",
        "render_as_batch",
        "include_schemas",
        "include_object",
        "include_name_from_type",
    ):
        if legacy_key in kwargs:
            opts[legacy_key] = kwargs.pop(legacy_key)
    if opts.get("literal_binds") and "as_sql" not in opts:
        opts["as_sql"] = True
    kwargs["opts"] = opts
    return _Real_MC_configure(cls, **kwargs)


_Real_MC.configure = classmethod(_patched_mc_configure)  # type: ignore[method-assign,assignment]


# ---- httpx ASGITransport compatibility shim ----
# The test fixture builds an ``httpx.Client(transport=httpx.ASGITransport(...))``
# (sync Client) which dispatches via ``transport.handle_request``. httpx 0.28
# dropped that method and exposes only ``handle_async_request`` returning a
# response whose stream is an ``ASGIResponseStream`` (AsyncByteStream). The
# sync Client refuses to consume async streams, so we restore a sync shim
# that runs the async path in a private loop AND swaps the async stream
# for a sync ``ByteStream`` holding the fully-buffered body. This keeps the
# test contract intact without changing the test code itself.
def _install_asgi_handle_request() -> None:
    import httpx
    from httpx._transports.asgi import ASGIResponseStream

    if hasattr(httpx.ASGITransport, "handle_request"):
        return

    _async_handler = httpx.ASGITransport.handle_async_request

    def _handle_request(self, request):  # noqa: ANN001
        async def _drive() -> httpx.Response:
            resp = await _async_handler(self, request)
            # Drain the async body into memory so we can hand a sync stream
            # back to httpx.Client.
            if isinstance(resp.stream, ASGIResponseStream):
                body = b"".join([chunk async for chunk in resp.stream])
                resp.stream = httpx.ByteStream(body)
            return resp

        return asyncio.run(_drive())

    httpx.ASGITransport.handle_request = _handle_request  # type: ignore[attr-defined]


_install_asgi_handle_request()


@pytest.fixture(autouse=True)
def _reset_taskq_db():
    """Drop + recreate all ORM tables before every test.

    The test file builds ``TaskRepository()`` and ``TaskService()``
    directly (in-process) for several assertions, while the HTTP tests
    go through a fresh ``create_app()`` per test. Both share the same
    in-memory SQLite engine; this fixture keeps the data fresh.
    """
    from taskq.repository.tasks import reset_db

    reset_db()
    yield
