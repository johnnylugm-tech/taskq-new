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

    httpx.ASGITransport.handle_request = _handle_request


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
