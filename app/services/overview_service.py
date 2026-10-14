"""Numbers the browser used to compute from every record (Todo-Pilot §22 / UX-9).

The dashboard and the menu badges get them here, counted by the database, so
login no longer downloads every issue, task and approval. Same definitions as
the frontend's lib/issue-work-state.ts:
  in progress     = not closed/cancelled, and not awaiting close
  awaiting close  = `resolved`, or "Siap ditutup" (ready_to_close_clause)
"""

from __future__ import annotations

from datetime import date
from typing import Any, Dict, Optional

from sqlalchemy import case, func, not_, or_
from sqlalchemy.orm import Session

from app.models.approval import ApprovalRequest
from app.models.issue import Issue
from app.models.task import Task
from app.services.auth_service import has_permission
from app.services.issue_closure_service import ISSUE_FINAL, TASK_TERMINAL
from app.services.issue_service import ready_to_close_clause
from app.services.outlet_scope_service import scoped_query

ISSUE_READ_MODULES = ("issues", "dashboard", "maintenance", "qa", "guest-service", "it-support")


def _awaiting():
    return or_(Issue.status == "resolved", ready_to_close_clause())


def _in_progress():
    return (Issue.status.notin_(("closed", "cancelled"))) & not_(_awaiting())


def _v(x) -> str:
    return x.value if hasattr(x, "value") else str(x)


def overview(db: Session, user) -> Dict[str, Any]:
    q = scoped_query(db, Issue, user)
    by_category = {_v(c): n for c, n in q.with_entities(Issue.category, func.count()).group_by(Issue.category)}
    by_outlet = {(o or "—"): n for o, n in q.with_entities(Issue.outlet, func.count()).group_by(Issue.outlet)}
    urgent = (q.filter(_in_progress(), Issue.priority.in_(("critical", "high")))
              .order_by(case((Issue.priority == "critical", 0), else_=1), Issue.created_at.desc(), Issue.id.desc())
              .limit(5).all())
    out: Dict[str, Any] = {
        "issues": {
            "total": q.count(),
            "inProgress": q.filter(_in_progress()).count(),
            "awaitingClose": q.filter(_awaiting()).count(),
            "slaBreached": q.filter(Issue.status.notin_(tuple(ISSUE_FINAL)), Issue.due_date.isnot(None),
                                    Issue.due_date < date.today()).count(),
            "byCategory": by_category,
            "byOutlet": by_outlet,
            "urgent": [{"id": str(i.id), "number": i.number, "title": i.title, "outlet": i.outlet,
                        "priority": _v(i.priority)} for i in urgent],
        },
        "tasks": None,
        "approvals": None,
    }
    if has_permission(user, "tasks"):
        out["tasks"] = {"active": _active_tasks(db, user)}
    if has_permission(user, "approvals"):
        pending = scoped_query(db, ApprovalRequest, user).filter(ApprovalRequest.status == "pending")
        out["approvals"] = {
            "pending": pending.count(),
            "pendingByType": {_v(t): n for t, n in
                              pending.with_entities(ApprovalRequest.type, func.count()).group_by(ApprovalRequest.type)},
        }
    return out


def _active_tasks(db: Session, user) -> int:
    return scoped_query(db, Task, user).filter(Task.status.notin_(tuple(TASK_TERMINAL))).count()


def counts(db: Session, user) -> Dict[str, Optional[int]]:
    """Menu badges; None where the role cannot open that list."""
    can_issues = any(has_permission(user, m) for m in ISSUE_READ_MODULES)
    return {
        "issuesInProgress": scoped_query(db, Issue, user).filter(_in_progress()).count() if can_issues else None,
        "tasksActive": _active_tasks(db, user) if has_permission(user, "tasks") else None,
        "approvalsPending": scoped_query(db, ApprovalRequest, user).filter(ApprovalRequest.status == "pending").count()
        if has_permission(user, "approvals") else None,
    }
