"""RFC 7807 Problem exception — FR-01/FR-10 error surface.

[FR-01] Every non-2xx response surfaces as application/problem+json
without leaking stack / SQL / path / schema. The body is a strict
whitelist of fields.

[FR-10] The body MUST additionally carry ``instance`` (the request
path that triggered the error) and ``correlation_id`` (mirrored from
the ``X-Correlation-Id`` request header when present, otherwise
minted at the api layer). The six top-level fields per AC-10.2 are:
``type``, ``title``, ``status``, ``detail``, ``instance``,
``correlation_id``.

Citations: SPEC.md §3 FR-01, §3 FR-10, §8 #19; RFC 7807 §3.1;
SAD.md §4 api/problem.

This module lives in the api layer (NOT taskq.errors) to honour the
NFR-06 architecture constraint that ``taskq.api`` and ``taskq.errors``
are independent — the api raises Problems; errors is a leaf module
with no dependents in the upward-import direction.
"""
from __future__ import annotations

# pragma: no error-handling

from typing import Any, Dict, Optional

# RFC 7807 §3 reserves ``application/problem+json`` as the canonical
# media type for problem documents. Both the exception-handler layer
# and the rate-limit middleware build JSONResponses with this type;
# the constant lives here (next to ``Problem``) so there is exactly
# one place to change it if the spec evolves.
CONTENT_TYPE = "application/problem+json"

# Optional body fields. Empty strings / None mean "omit the field"
# so the contract surface stays a clean whitelist (NFR-02).
_OPTIONAL_BODY_FIELDS = ("detail", "instance", "correlation_id")


class Problem(Exception):
    """RFC 7807 problem document raised from anywhere in the api stack.

    Fields:
        type           — URI reference identifying the problem type
        title          — short, human-readable summary
        status         — HTTP status code (must match response status)
        detail         — human-readable explanation (no stack/SQL/path/schema)
        instance       — URI reference for this occurrence (request path)
        correlation_id — operator-supplied or minted request id; mirrored
                         to the X-Correlation-Id response header (AC-10.4)
    """

    def __init__(
        self,
        status: int,
        title: str,
        detail: str = "",
        type: str = "about:blank",
        instance: Optional[str] = None,
        correlation_id: Optional[str] = None,
        extra: Optional[Dict[str, Any]] = None,
    ) -> None:
        self.status = status
        self.title = title
        self.detail = detail
        self.type = type
        self.instance = instance
        self.correlation_id = correlation_id
        self.extra = extra or {}
        super().__init__(f"{status} {title}: {detail}")

    def to_dict(self) -> Dict[str, Any]:
        """Serialise to the FR-10 / RFC 7807 body shape."""
        out: Dict[str, Any] = {
            "type": self.type,
            "title": self.title,
            "status": self.status,
            "detail": self.detail,
            "instance": self.instance,
            "correlation_id": self.correlation_id,
        }
        # Drop empty optional fields; merge ``extra`` last so the
        # canonical whitelist cannot be silently overridden.
        return {
            key: value
            for key, value in out.items()
            if value or key not in _OPTIONAL_BODY_FIELDS
        } | {key: value for key, value in self.extra.items() if key not in out}


__all__ = ["Problem", "CONTENT_TYPE"]
