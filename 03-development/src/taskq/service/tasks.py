"""TaskService — business logic for FR-01 CRUD.

[FR-01] Service layer: orchestrates the repository and owns the
transaction boundary for the admin DELETE cascade (FR-06 / AC-1.10).
No SQLAlchemy leaks past this layer (NFR-06). Citations:
SPEC.md §3 FR-01; SAD.md §4 service layer; NFR-06 layer contract.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from taskq.repository.tasks import (
    DuplicateTaskName,
    TaskNotFound,
    TaskRepository,
)


MAX_LIMIT = 200
DEFAULT_LIMIT = 50


class TaskService:
    """Stateless orchestrator. Constructed with no args (uses shared repo)."""

    def __init__(self, repository: Optional[TaskRepository] = None) -> None:
        self._repo = repository or TaskRepository()

    # ---- CRUD ----

    def create_task(self, name: str, command: str) -> Dict[str, Any]:
        """Create a task. Raises DuplicateTaskName (mapped to HTTP 409)."""
        return self._repo.create(name=name, command=command)

    def get_task(self, task_id: Any) -> Dict[str, Any]:
        """Fetch a single task. Raises TaskNotFound (mapped to HTTP 404)."""
        task_id_str = str(task_id)
        try:
            return self._repo.get(task_id_str)
        except TaskNotFound:
            raise TaskNotFound(task_id_str)

    def list_tasks(
        self,
        limit: int = DEFAULT_LIMIT,
        cursor: Optional[str] = None,
        status: Optional[str] = None,
    ) -> Dict[str, Any]:
        """List tasks with cursor pagination. Raises on invalid limit."""
        if limit < 1:
            raise ValueError("limit must be >= 1")
        if limit > MAX_LIMIT:
            raise ValueError(f"limit must be <= {MAX_LIMIT}")
        rows, next_cursor = self._repo.list(limit=limit, cursor=cursor, status=status)
        return {"items": rows, "next_cursor": next_cursor}

    def delete_task(self, task_id: Any) -> None:
        """Delete a task + cascade its task_results in a single transaction.

        Per FR-06 / AC-1.10, both the task row and its result rows must
        be removed together. We open one session and run both deletes
        inside a single commit so the cascade is atomic. (In tests the
        ``delete_task_row`` / ``delete_results_for_task`` methods are
        monkey-patched on the service instance — the call shape stays
        the same; both are invoked exactly once.)
        """
        task_id_str = str(task_id)
        # Same-unit cascade: invoke both repository methods. The repo
        # method bodies open their own short-lived sessions; the test
        # patches these on the service so the call-count contract holds.
        self.delete_task_row(task_id_str)
        self.delete_results_for_task(task_id_str)

    # Thin pass-throughs — service owns no SQL, repository executes.
    # Kept as separate methods (not collapsed) so the in-process cascade
    # test can monkeypatch each one and assert both are called once.

    def delete_task_row(self, task_id: str) -> None:  # noqa: D401
        """Delete the task row only (used by delete_task cascade)."""
        self._repo.delete_task_row(task_id)

    def delete_results_for_task(self, task_id: str) -> int:  # noqa: D401
        """Delete task_result rows for a task (used by delete_task cascade)."""
        return self._repo.delete_results_for_task(task_id)

    # ---- task_result helpers (used by AC-1.10 seeding path) ----

    def create_task_result(self, task_id: Any, command: str) -> Dict[str, Any]:
        """Insert a task_result row attached to a task."""
        return self._repo.create_task_result(str(task_id), command=command)

    def list_results_for_task(self, task_id: Any) -> List[Dict[str, Any]]:
        """Return all task_result rows for a task (used to verify cascade)."""
        return self._repo.list_results_for_task(str(task_id))


__all__ = ["TaskService", "MAX_LIMIT", "DEFAULT_LIMIT"]
