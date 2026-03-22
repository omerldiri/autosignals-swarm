"""
Task board with dependency DAG.

Stores tasks in .swarm/tasks.json with automatic dependency resolution.
When a task completes, dependents auto-unblock.

Task schema:
    {
        "id": "<string>",
        "title": "<string>",
        "status": "blocked|ready|in_progress|done|failed",
        "assigned_to": "<worker_name>|null",
        "blocked_by": ["<task_id>", ...],
        "result": { ... } | null,
        "created_at": "<iso8601>",
        "updated_at": "<iso8601>"
    }
"""

import json
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional
import threading


SWARM_DIR = Path(".swarm")
TASKS_FILE = SWARM_DIR / "tasks.json"
_lock = threading.Lock()


def _load_tasks() -> Dict[str, dict]:
    """Load task board from disk."""
    SWARM_DIR.mkdir(parents=True, exist_ok=True)
    if TASKS_FILE.exists():
        with open(TASKS_FILE) as f:
            return json.load(f)
    return {}


def _save_tasks(tasks: Dict[str, dict]):
    """Save task board to disk."""
    SWARM_DIR.mkdir(parents=True, exist_ok=True)
    with open(TASKS_FILE, "w") as f:
        json.dump(tasks, f, indent=2)


def _resolve_dependencies(tasks: Dict[str, dict]):
    """Auto-unblock tasks whose dependencies are all done."""
    for task_id, task in tasks.items():
        if task["status"] == "blocked":
            blockers = task.get("blocked_by", [])
            all_done = all(
                tasks.get(b, {}).get("status") == "done"
                for b in blockers
            )
            if all_done:
                task["status"] = "ready"
                task["updated_at"] = datetime.now().isoformat()


def add_task(
    task_id: str,
    title: str,
    blocked_by: Optional[List[str]] = None,
    metadata: Optional[dict] = None,
) -> dict:
    """Add a new task to the board."""
    with _lock:
        tasks = _load_tasks()
        now = datetime.now().isoformat()
        blockers = blocked_by or []

        # Check if any blockers are not-done
        has_blockers = any(
            tasks.get(b, {}).get("status") != "done"
            for b in blockers
        )

        task = {
            "id": task_id,
            "title": title,
            "status": "blocked" if has_blockers else "ready",
            "assigned_to": None,
            "blocked_by": blockers,
            "result": None,
            "metadata": metadata or {},
            "created_at": now,
            "updated_at": now,
        }

        tasks[task_id] = task
        _save_tasks(tasks)
        return task


def claim_task(task_id: str, worker_name: str) -> bool:
    """Claim a ready task for a worker. Returns True if successful."""
    with _lock:
        tasks = _load_tasks()
        task = tasks.get(task_id)
        if not task or task["status"] != "ready":
            return False

        task["status"] = "in_progress"
        task["assigned_to"] = worker_name
        task["updated_at"] = datetime.now().isoformat()
        _save_tasks(tasks)
        return True


def complete_task(task_id: str, result: Optional[dict] = None) -> dict:
    """Mark a task as done and resolve dependencies."""
    with _lock:
        tasks = _load_tasks()
        task = tasks.get(task_id)
        if not task:
            raise ValueError(f"Task {task_id} not found")

        task["status"] = "done"
        task["result"] = result
        task["updated_at"] = datetime.now().isoformat()

        # Auto-unblock dependents
        _resolve_dependencies(tasks)
        _save_tasks(tasks)
        return task


def fail_task(task_id: str, error: str) -> dict:
    """Mark a task as failed."""
    with _lock:
        tasks = _load_tasks()
        task = tasks.get(task_id)
        if not task:
            raise ValueError(f"Task {task_id} not found")

        task["status"] = "failed"
        task["result"] = {"error": error}
        task["updated_at"] = datetime.now().isoformat()
        _save_tasks(tasks)
        return task


def get_ready_tasks() -> List[dict]:
    """Get all tasks that are ready to be claimed."""
    tasks = _load_tasks()
    return [t for t in tasks.values() if t["status"] == "ready"]


def get_tasks_by_status(status: str) -> List[dict]:
    """Get all tasks with a given status."""
    tasks = _load_tasks()
    return [t for t in tasks.values() if t["status"] == status]


def get_all_tasks() -> Dict[str, dict]:
    """Get the full task board."""
    return _load_tasks()


def reset_board():
    """Clear the entire task board."""
    with _lock:
        _save_tasks({})
