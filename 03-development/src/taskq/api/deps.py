"""Single canonical FastAPI dependency for API-key scope enforcement.

[FR-04] Every ``/v1`` route MUST pass through the single
``require_scope`` dependency declared in this module (AC-4.3). The
per-route duplicate ``_require_scope`` helpers that lived inside
``taskq.api.routes.tasks`` / ``taskq.api.routes.runs`` were the
FR-01 baseline bug; FR-04 consolidates them here so a structural
introspection of the FastAPI dependant tree sees exactly one
scope-enforcing dependency on every /v1 route.

Behaviour:
  * Missing / invalid key    -> ``Problem(401)`` (SPEC.md §8 #5)
  * Valid key, wrong scope   -> ``Problem(403)`` with a generic body
    that does NOT reveal whether the target resource exists
    (SPEC.md §8 #6, NFR-02)
  * Valid key, correct scope -> ``{"scope": <scope>, "key_id": <key>}``

The factory returns a closure named ``_dep`` defined inside
``require_scope``, so its ``__qualname__`` contains
``"require_scope"`` and its ``__module__`` is ``taskq.api.deps`` —
this is exactly what AC-4.3 introspects to assert the single-dep
invariant.

Citations: SPEC.md §3 FR-04, §8 #5, §8 #6; SAD.md §4 api/deps;
NFR-02 (no resource-existence leak in 403 body); NFR-06 (layering —
the dependency lives in ``taskq.api``).
"""
from __future__ import annotations

from typing import Callable, Dict, Optional

from fastapi import Header

from taskq.api.problem import Problem
from taskq.service.auth import InsufficientScope, InvalidAPIKey, verify_api_key


def require_scope(scope: str) -> Callable[..., Dict[str, str]]:
    """Build a FastAPI dependency that enforces API-key ``scope``.

    Usage::

        @router.post("")
        def create_task(
            body: TaskCreate,
            _auth: Dict[str, str] = Depends(require_scope("write")),
            service: TaskService = Depends(_get_service),
        ):
            ...

    The returned callable reads ``X-API-Key`` from the request headers,
    delegates the scope check to ``taskq.service.auth.verify_api_key``,
    and translates the service-layer exceptions into RFC 7807
    problem documents. The HTTP layer never re-implements scope
    hierarchy — that lives in the service layer (AC-4.1).
    """

    def _dep(
        x_api_key: Optional[str] = Header(default=None, alias="X-API-Key"),
    ) -> Dict[str, str]:
        try:
            return verify_api_key(x_api_key, scope_required=scope)
        except InvalidAPIKey as exc:
            raise Problem(
                status=401,
                title="Unauthorized",
                detail="Invalid or missing API key.",
                type="about:blank",
            ) from exc
        except InsufficientScope as exc:
            # NFR-02 / SPEC §8 #6: body MUST NOT reveal whether the
            # target resource exists. The handler never reaches the
            # task-id lookup because this dependency fires first.
            raise Problem(
                status=403,
                title="Forbidden",
                detail="Operation not permitted.",
                type="about:blank",
            ) from exc

    return _dep


__all__ = ["require_scope"]