"""RFC 7807 Problem exception — FR-01/FR-10 error surface.

[FR-01] Every non-2xx response surfaces as application/problem+json
without leaking stack / SQL / path / schema. The body is a strict
whitelist of fields. Citations: SPEC.md §3 FR-01, §8 #4-#8, §10 FR-10;
RFC 7807.

This module lives in the api layer (NOT taskq.errors) to honour the
NFR-06 architecture constraint that ``taskq.api`` and ``taskq.errors``
are independent — the api raises Problems; errors is a leaf module
with no dependents in the upward-import direction.
"""
from __future__ import annotations

from typing import Any, Dict, Optional


class Problem(Exception):
    """RFC 7807 problem document raised from anywhere in the api stack.

    Fields:
        type     — URI reference identifying the problem type
        title    — short, human-readable summary
        status   — HTTP status code (must match response status)
        detail   — human-readable explanation (no stack/SQL/path/schema)
        instance — optional URI reference for this occurrence
    """

    def __init__(
        self,
        status: int,
        title: str,
        detail: str = "",
        type: str = "about:blank",
        instance: Optional[str] = None,
        extra: Optional[Dict[str, Any]] = None,
    ) -> None:
        self.status = status
        self.title = title
        self.detail = detail
        self.type = type
        self.instance = instance
        self.extra = extra or {}
        super().__init__(f"{status} {title}: {detail}")

    def to_dict(self) -> Dict[str, Any]:
        out: Dict[str, Any] = {
            "type": self.type,
            "title": self.title,
            "status": self.status,
        }
        if self.detail:
            out["detail"] = self.detail
        if self.instance:
            out["instance"] = self.instance
        for key, value in self.extra.items():
            if key not in out:
                out[key] = value
        return out


__all__ = ["Problem"]
