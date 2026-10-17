import uuid
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sqlalchemy.orm import Session

from app.database import get_db
from app.services.outlet_scope_service import assert_can_access, scoped_query
from app.models.task import Task
from app.schemas.task import TaskResponse, UpdateTaskRequest
from app.services.audit_service import write_audit
from app.services.auth_service import require_permission, UserResponse, get_current_user
from app.services.pagination import Page, like_pattern, paginate
from sqlalchemy import or_
from app.services.issue_closure_service import TASK_TERMINAL, on_child_completed
from app.services.assignee_service import apply_assignee
from app.services.notification_service import notify_task_assigned

router = APIRouter(prefix="/api/tasks", tags=["tasks"])


def _task_to_response(task: Task, issue_status: Optional[str] = None) -> TaskResponse:
    return TaskResponse(
        id=str(task.id),
        number=task.number,
        title=task.title,
        description=task.description or "",
        status=task.status.value if hasattr(task.status, "value") else str(task.status),
        priority=task.priority.value if hasattr(task.priority, "value") else str(task.priority),
        assignee=task.assignee or "Unassigned",
        assigneeUserId=str(task.assignee_user_id) if task.assignee_user_id else None,
        dueDate=task.due_date.isoformat() if task.due_date else None,
        outlet=task.outlet or "",
        issueId=str(task.issue_id),
        issueNumber=task.issue_number,
        issueStatus=issue_status if issue_status is not None else (
            (task.issue.status.value if hasattr(task.issue.status, "value") else str(task.issue.status))
            if task.issue is not None else None),
    )


@router.get("", response_model=List[TaskResponse])
def list_tasks(
    response: Response,
    status: Optional[str] = Query(None),
    issue_id: Optional[uuid.UUID] = Query(None),
    q: Optional[str] = Query(None, description="Search number, title, issue number or assignee"),
    priority: Optional[str] = Query(None),
    late: bool = Query(False, description="Only open tasks past their due date"),
    outlet: Optional[str] = Query(None, description="One outlet (the top bar's outlet filter)"),
    page: Page = Depends(),
    db: Session = Depends(get_db),
    current_user: UserResponse = Depends(require_permission("tasks", "view")),
):
    """List all Tasks, optionally filtered by status (FR-9 note: no POST — Tasks come from Issues); paged (Todo-Pilot §22)."""
    query = scoped_query(db, Task, current_user)
    if status:
        query = query.filter(Task.status == status)
    if issue_id:
        query = query.filter(Task.issue_id == issue_id)
    if priority:
        query = query.filter(Task.priority == priority)
    if outlet:
        query = query.filter(Task.outlet == outlet)
    if late:
        from datetime import date as _date
        from app.services.issue_closure_service import TASK_TERMINAL
        query = query.filter(Task.status.notin_(tuple(TASK_TERMINAL)), Task.due_date.isnot(None), Task.due_date < _date.today())
    if q and q.strip():
        p = like_pattern(q)
        query = query.filter(or_(Task.number.ilike(p), Task.title.ilike(p), Task.issue_number.ilike(p), Task.assignee.ilike(p)))
    tasks = paginate(query, page, response, Task.created_at.desc(), Task.id.desc())
    # Parent statuses in one query, not one per task (Todo-Pilot §22).
    from app.models.issue import Issue
    ids = {t.issue_id for t in tasks if t.issue_id}
    statuses = {i: (s.value if hasattr(s, "value") else str(s))
                for i, s in db.query(Issue.id, Issue.status).filter(Issue.id.in_(ids))} if ids else {}
    return [_task_to_response(t, statuses.get(t.issue_id)) for t in tasks]


@router.get("/{task_id}", response_model=TaskResponse)
def get_task(task_id: str, db: Session = Depends(get_db), current_user: UserResponse = Depends(require_permission("tasks", "view"))):
    """One Task — re-read after a failed update instead of the whole list (Todo-Pilot §22)."""
    try:
        uuid.UUID(task_id)
    except ValueError:
        raise HTTPException(status_code=404, detail="Task not found")
    task = scoped_query(db, Task, current_user).filter(Task.id == task_id).first()
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    return _task_to_response(task)


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
        # The Issue is finished: its Tasks are history until it is reopened (Todo-Pilot §23).
        if task.issue is not None and req.status != old_status:
            issue_status = task.issue.status.value if hasattr(task.issue.status, "value") else str(task.issue.status)
            if issue_status in ("closed", "cancelled"):
                raise HTTPException(
                    status_code=409,
                    detail=f"Issue {task.issue.number} is {issue_status} — reopen it before changing its tasks.",
                )
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
    if req.assigneeUserId is not None or req.assignee is not None:
        if apply_assignee(db, task, user_id=req.assigneeUserId, name=req.assignee):
            notify_task_assigned(db, task)
    if req.priority is not None:
        task.priority = req.priority

    db.commit()
    db.refresh(task)
    return _task_to_response(task)
