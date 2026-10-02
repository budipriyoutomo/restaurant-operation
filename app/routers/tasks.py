from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.database import get_db
from app.services.outlet_scope_service import assert_can_access, scoped_query
from app.models.task import Task
from app.schemas.task import TaskResponse, UpdateTaskRequest
from app.services.audit_service import write_audit
from app.services.auth_service import require_permission, UserResponse, get_current_user
from app.services.issue_closure_service import TASK_TERMINAL, on_child_completed

router = APIRouter(prefix="/api/tasks", tags=["tasks"])


def _task_to_response(task: Task) -> TaskResponse:
    return TaskResponse(
        id=str(task.id),
        number=task.number,
        title=task.title,
        description=task.description or "",
        status=task.status.value if hasattr(task.status, "value") else str(task.status),
        priority=task.priority.value if hasattr(task.priority, "value") else str(task.priority),
        assignee=task.assignee or "Unassigned",
        dueDate=task.due_date.isoformat() if task.due_date else None,
        outlet=task.outlet or "",
        issueId=str(task.issue_id),
        issueNumber=task.issue_number,
    )


@router.get("", response_model=List[TaskResponse])
def list_tasks(
    status: Optional[str] = Query(None),
    db: Session = Depends(get_db),
    current_user: UserResponse = Depends(require_permission("tasks", "view")),
):
    """List all Tasks, optionally filtered by status (FR-9 note: no POST — Tasks come from Issues)."""
    query = scoped_query(db, Task, current_user)
    if status:
        query = query.filter(Task.status == status)
    tasks = query.order_by(Task.created_at.desc()).all()
    return [_task_to_response(t) for t in tasks]


@router.patch("/{task_id}", response_model=TaskResponse)
def update_task(task_id: str, req: UpdateTaskRequest, db: Session = Depends(get_db), current_user: UserResponse = Depends(require_permission("tasks", "view"))):
    """Update Task status (FR-11).

    When the last open Task of an Issue is resolved/closed, the Issue rolls up to
    `resolved` unless a pending Approval or an active Work Order still blocks it
    (Todo-Next §2.1), and its managers are told it is ready to close
    (Todo-Pilot §1, issue_closure_service.on_child_completed).
    """
    task = scoped_query(db, Task, current_user).filter(Task.id == task_id).first()
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")

    if req.status is not None:
        old_status = task.status.value if hasattr(task.status, "value") else str(task.status)
        task.status = req.status
        if req.status != old_status:
            write_audit(
                db,
                table_name="tasks",
                record_id=str(task.id),
                action="status_change",
                old_value={"status": old_status, "number": task.number},
                new_value={"status": req.status, "number": task.number},
                performed_by=current_user.email,
            )
            # Only a Task that just *finished* can complete its Issue's work
            # (resolved → closed is not news; neither is a reopen).
            if task.issue is not None and req.status in TASK_TERMINAL and old_status not in TASK_TERMINAL:
                on_child_completed(db, task.issue)
    if req.assignee is not None:
        task.assignee = req.assignee
    if req.priority is not None:
        task.priority = req.priority

    db.commit()
    db.refresh(task)
    return _task_to_response(task)
