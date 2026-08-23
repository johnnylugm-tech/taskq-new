"""taskq.models — SQLAlchemy ORM models.

[FR-01] Models layer (leaves; no upward imports). Citations: SAD.md §4, SPEC.md §3 FR-01.

R2 fix: explicit model imports so ``Base.metadata`` is populated whenever
*any* module does ``from taskq.models import Base``. Without these, a
test that imports only ``taskq.repository.tasks`` (which in turn imports
``taskq.models.base`` and ``taskq.models.task``) gets a ``Base.metadata``
that knows about ``Task`` and ``TaskResult`` but NOT ``APIKey`` — the
``reset_db`` autouse fixture then runs ``Base.metadata.create_all`` and
silently omits the ``api_keys`` table. The next test then crashes with
``sqlite3.OperationalError: no such table: api_keys`` (Round 2 repro:
test_nfr01_ac1_get_p95_under_30ms, test_nfr01_ac2_list_p95_under_80ms,
test_nfr04_ac3_api_key_plaintext_once_no_persist, etc.).

Each ``Base`` subclass registers itself on import via SQLAlchemy's
metaclass machinery — there is no separate ``register()`` call to make.
"""
from taskq.models.api_key import APIKey  # noqa: F401 — registers on Base
from taskq.models.base import Base  # noqa: F401 — re-export
from taskq.models.task import Task  # noqa: F401 — registers on Base
from taskq.models.task_result import TaskResult  # noqa: F401 — registers on Base

__all__ = ["APIKey", "Base", "Task", "TaskResult"]
