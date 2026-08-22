"""taskq.errors — RFC 7807 error contract (leaf module).

[FR-01] Reserved as an independent leaf module under NFR-06: the
``taskq.api`` and ``taskq.errors`` package must not import each other
(see ``.importlinter`` contract ``fr01-config-errors-independence``).
The concrete Problem class and FastAPI exception handlers live in
``taskq.api.problem`` / ``taskq.api.handlers`` — this package stays
empty by design so the layer boundary is enforceable.
"""
