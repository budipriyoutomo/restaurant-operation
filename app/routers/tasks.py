from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.task import Task
from app.schemas.task import TaskResponse, UpdateTaskRequest

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
):
    """List all Tasks, optionally filtered by status (FR-9 note: no POST — Tasks come from Issues)."""
    query = db.query(Task)
    if status:
        query = query.filter(Task.status == status)
    tasks = query.order_by(Task.created_at.desc()).all()
    return [_task_to_response(t) for t in tasks]


@router.patch("/{task_id}", response_model=TaskResponse)
def update_task(task_id: str, req: UpdateTaskRequest, db: Session = Depends(get_db)):
    """Update Task status (FR-11). Intentionally does not cascade to parent Issue in MVP."""
    task = db.query(Task).filter(Task.id == task_id).first()
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")

    if req.status is not None:
        task.status = req.status
    if req.assignee is not None:
        task.assignee = req.assignee
    if req.priority is not None:
        task.priority = req.priority

    db.commit()
    db.refresh(task)
    return _task_to_response(task)
