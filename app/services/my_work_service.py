""""Pekerjaan Saya" — what is waiting for the signed-in user (Todo-Pilot §19 / UX-6).

Computed on the server so the home screen does not depend on the browser
having loaded every record (item 22 moves lists to server pagination).
Every section is outlet-scoped (scoped_query) and only present when the
user's role can open that module; otherwise it is None.
"""

from __future__ import annotations

import uuid
from typing import Any, Dict, List, Optional

from sqlalchemy.orm import Session

from app.models.approval import ApprovalRequest
from app.models.asset import WorkOrder
from app.models.guest_case import GuestCase
from app.models.issue import Issue
from app.models.task import Task
from app.services.auth_service import UserResponse, has_permission
from app.services.issue_closure_service import is_ready_to_close
from app.services.outlet_scope_service import scoped_query

_PRIORITY_RANK = {"critical": 0, "high": 1, "medium": 2, "low": 3}
_TASK_DONE = ("resolved", "closed", "cancelled")
_WO_DONE = ("completed", "cancelled")


def _v(x) -> str:
    return x.value if hasattr(x, "value") else str(x)


def _iso(d) -> Optional[str]:
    return d.isoformat() if d else None


def _section(items: List[Dict[str, Any]], limit: int) -> Dict[str, Any]:
    return {"count": len(items), "items": items[:limit]}


def _awaiting_my_decision(db: Session, user: UserResponse) -> List[Dict[str, Any]]:
    """Pending approvals whose active step this user may decide (same rule as
    approval_service.decide_current_step: the step's role = my approval tier),
    minus steps delegated to someone else."""
    me = uuid.UUID(user.id)
    out = []
    pending = scoped_query(db, ApprovalRequest, user).filter(ApprovalRequest.status == "pending").all()
    for a in pending:
        step = next((s for s in a.steps if s.step_order == a.current_step_order), None)
        if step is None or _v(step.approver_role) != user.approval_tier:
            continue
        if step.approver_user_id is not None and step.approver_user_id != me:
            continue
        out.append({
            "id": str(a.id), "number": a.number, "title": a.title, "type": _v(a.type),
            "outlet": a.outlet or "", "amount": a.amount, "currency": a.currency,
            "stepOrder": a.current_step_order, "stepCount": len(a.steps),
            "waitingSince": _iso(a.current_step_since), "escalated": bool(a.escalated),
        })
    out.sort(key=lambda x: x["waitingSince"] or "")      # oldest first
    return out


def _my_work_orders(db: Session, user: UserResponse) -> List[Dict[str, Any]]:
    rows = (scoped_query(db, WorkOrder, user)
            .filter(WorkOrder.assignee_user_id == uuid.UUID(user.id), WorkOrder.status.notin_(_WO_DONE))
            .all())
    items = [{
        "id": str(w.id), "number": w.number, "title": w.title, "assetName": w.asset_name,
        "outlet": w.outlet or "", "status": _v(w.status), "priority": _v(w.priority),
        "scheduledDate": _iso(w.scheduled_date),
    } for w in rows]
    items.sort(key=lambda x: (_PRIORITY_RANK.get(x["priority"], 9), x["scheduledDate"] or "9999"))
    return items


def _my_tasks(db: Session, user: UserResponse) -> List[Dict[str, Any]]:
    rows = (scoped_query(db, Task, user)
            .filter(Task.assignee_user_id == uuid.UUID(user.id), Task.status.notin_(_TASK_DONE))
            .all())
    items = [{
        "id": str(t.id), "number": t.number, "title": t.title, "issueId": str(t.issue_id),
        "issueNumber": t.issue_number, "outlet": t.outlet or "", "status": _v(t.status),
        "priority": _v(t.priority), "dueDate": _iso(t.due_date),
    } for t in rows]
    items.sort(key=lambda x: (x["dueDate"] or "9999", _PRIORITY_RANK.get(x["priority"], 9)))
    return items


def _ready_to_close(db: Session, user: UserResponse) -> List[Dict[str, Any]]:
    rows = (scoped_query(db, Issue, user)
            .filter(Issue.status.in_(("open", "assigned", "in-progress", "resolved")))
            .order_by(Issue.created_at)
            .all())
    return [{"id": str(i.id), "number": i.number, "title": i.title, "outlet": i.outlet or "",
             "status": _v(i.status)} for i in rows if is_ready_to_close(i)]


def _guest_waiting(db: Session, user: UserResponse) -> List[Dict[str, Any]]:
    rows = (scoped_query(db, GuestCase, user)
            .join(Issue, GuestCase.issue_id == Issue.id)
            .filter(GuestCase.first_response_at.is_(None),
                    Issue.status.notin_(("resolved", "closed", "cancelled")))
            .order_by(GuestCase.reported_at)
            .all())
    return [{"id": str(c.id), "issueId": str(c.issue_id), "issueNumber": c.issue.number,
             "title": c.issue.title, "outlet": c.outlet, "channel": c.channel,
             "reportedAt": _iso(c.reported_at)} for c in rows]


def my_work(db: Session, user: UserResponse, limit: int = 20) -> Dict[str, Any]:
    def gate(ok: bool, fn):
        return _section(fn(db, user), limit) if ok else None

    return {
        "approvals":    gate(has_permission(user, "approvals", "manage"), _awaiting_my_decision),
        "workOrders":   gate(has_permission(user, "cmms") or has_permission(user, "maintenance"), _my_work_orders),
        "tasks":        gate(has_permission(user, "tasks"), _my_tasks),
        "readyToClose": gate(has_permission(user, "issues", "manage"), _ready_to_close),
        "guestWaiting": gate(has_permission(user, "guest-service"), _guest_waiting),
    }
