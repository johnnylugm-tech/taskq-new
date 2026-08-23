"""SQLAlchemy declarative base.

[FR-01] Provides the shared metadata + Base for all ORM models.
Citations: SAD.md §4 Models layer; SPEC.md §3 FR-01.
"""
from __future__ import annotations

# pragma: no error-handling

from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    """Shared declarative base for all taskq ORM models."""

    pass
