"""Pydantic schemas for FR-01 request/response shapes.

[FR-01] TaskCreate enforces the three validation rules from SPEC.md §7
(non-empty, <=1000 chars, no injection-blacklist characters); TaskRead
is the projection returned by GET/POST. Citations: SPEC.md §3 FR-01,
§7 (validation rules); SAD.md §4 api/schemas.
"""
from __future__ import annotations

import re
from datetime import datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator

# SPEC.md §7: combined body length cap of 1000 characters.
MAX_FIELD_LENGTH = 1000

# Conservative injection blacklist (SPEC.md §7 — injection-blacklist characters).
# Covers the typical shell / SQL / path-traversal glyphs.
_INJECTION_BLACKLIST = re.compile(r"[;'\"`\\$|&<>\n\r\x00]")


def _validate_text(value: str) -> str:
    if value is None:
        raise ValueError("must not be empty")
    if not isinstance(value, str):
        raise ValueError("must be a string")
    if len(value) == 0:
        raise ValueError("must not be empty")
    if len(value) > MAX_FIELD_LENGTH:
        raise ValueError(f"exceeds {MAX_FIELD_LENGTH} characters")
    if _INJECTION_BLACKLIST.search(value):
        raise ValueError("contains blacklisted characters")
    return value


class TaskCreate(BaseModel):
    """Request body for POST /v1/tasks.

    Both `name` and `command` are required, non-empty, <=1000 chars,
    and may not contain any injection-blacklist character. Pydantic
    turns validation failures into HTTP 422 (handled by the
    application/problem+json handler).
    """

    model_config = ConfigDict(extra="forbid")

    name: str = Field(..., min_length=1, max_length=MAX_FIELD_LENGTH)
    command: str = Field(..., min_length=1, max_length=MAX_FIELD_LENGTH)

    @field_validator("name", "command")
    @classmethod
    def _check_text(cls, v: str) -> str:  # noqa: D401
        return _validate_text(v)


class TaskRead(BaseModel):
    """Response body for POST /v1/tasks and GET /v1/tasks/{id}."""

    id: str
    name: str
    command: str
    status: str
    created_at: datetime
    task_id: Optional[str] = None  # for compatibility with body.get("task_id")


__all__ = ["TaskCreate", "TaskRead", "MAX_FIELD_LENGTH"]
