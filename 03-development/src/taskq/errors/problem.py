"""Problem exception — RFC 7807 surface for all non-2xx responses.

[FR-01] Every non-2xx in FR-01 must surface as application/problem+json
without leaking stack / SQL / path / schema. The body is a strict whitelist
of fields. Citations: SPEC.md §3 FR-01, §8 #4, §8 #5, §8 #6, §8 #7, §8 #8;
RFC 7807.
"""
from __future__ import annotations

from typing import Any, Dict, Optional


class Problem(Exception):
    """RFC 7807 problem document raised from anywhere in the stack.

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
