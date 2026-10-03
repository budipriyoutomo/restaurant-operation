"""Issue service — the single entry point for all Issue creation and retrieval.

This is where the auto-generation logic lives: creating an Issue here
can automatically produce a Task and/or an ApprovalRequest, fully linked.
"""

import uuid
from datetime import date, datetime
from typing import List, Optional

from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.category_defaults import get_approval_type
from app.models.approval import ApprovalRequest, ApprovalNumberSequence
from app.models.asset import Asset, WorkOrder, WorkOrderNumberSequence
from app.models.enums import IssueStatusEnum, PriorityEnum, TaskStatusEnum, WorkOrderStatusEnum
from app.models.issue import Issue, IssueNumberSequence
from app.models.task import Task, TaskNumberSequence
from app.schemas.issue import CreateIssueRequest, IssueResponse, UpdateIssueRequest
from app.services.audit_service import write_audit
from app.services.guest_service import ensure_case as ensure_guest_case
from app.services.issue_closure_service import (
    ISSUE_CLOSING,
    ISSUE_FINAL,
    TASK_TERMINAL,
    WORK_ORDER_TERMINAL,
    can_close_issue,
)
from app.services.outlet_scope_service import resolve_outlet_id, scoped_query
from app.services.notification_service import (
    notify_issue_created,
    notify_issue_status_changed,
    notify_work_order_assigned,
)
from app.services.work_order_service import get_approval_threshold, needs_approval


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _next_number(db: Session, prefix: str, table: str) -> str:
    """Per-company document number (see app/services/numbering.py)."""
    from app.services.numbering import next_number
    return next_number(db, prefix, table)


def _value(v) -> str:
    return v.value if hasattr(v, "value") else str(v)


def _compute_sla_breach(due_date, status_value: str) -> bool:
    if due_date is None:
        return False
    if status_value in ISSUE_FINAL:
        return False
    today = date.today()
    return due_date < today


# Every title column is VARCHAR(500). A title that just fits on the Issue can
# still overflow on the records derived from it, because those carry a prefix —
# so trim the tail instead of failing the whole create.
TITLE_MAX = 500


def _derived_title(prefix: str, title: str) -> str:
    combined = f"{prefix}{title}"
    if len(combined) <= TITLE_MAX:
        return combined
    return combined[: TITLE_MAX - 1] + "\u2026"


def _parse_date(date_str: Optional[str]):
    """Parse an ISO date string ('2026-06-25') or return None."""
    if not date_str:
        return None
    try:
        return date.fromisoformat(date_str)
    except ValueError:
        return None


def _next_wo_number(db: Session) -> str:
    from app.services.numbering import next_number
    return next_number(db, "WO", "work_order_number_sequences")


def _issue_to_response(issue: Issue) -> IssueResponse:
    status_value = issue.status.value if hasattr(issue.status, "value") else str(issue.status)
    sla_breach = _compute_sla_breach(issue.due_date, status_value)

    # Find auto-generated corrective work order (if any)
    wo_id = None
    if hasattr(issue, "work_orders") and issue.work_orders:
        corrective = next(
            (w for w in issue.work_orders
             if (w.type.value if hasattr(w.type, "value") else str(w.type)) == "corrective"),
            None,
        )
        if corrective:
            wo_id = str(corrective.id)

    return IssueResponse(
        id=str(issue.id),
        number=issue.number,
        title=issue.title,
        description=issue.description or "",
        outlet=issue.outlet,
        category=issue.category.value if hasattr(issue.category, "value") else str(issue.category),
        priority=issue.priority.value if hasattr(issue.priority, "value") else str(issue.priority),
        status=status_value,
        assignee=issue.assignee or "Unassigned",
        dueDate=issue.due_date.isoformat() if issue.due_date else None,
        createdDate=issue.created_at.date().isoformat() if issue.created_at else date.today().isoformat(),
        slaBreach=sla_breach,
        taskIds=[str(t.id) for t in (issue.tasks or [])],
        approvalId=str(issue.approval.id) if issue.approval else None,
        workOrderId=wo_id,
        closureBlockers=[b.as_dict() for b in can_close_issue(issue).blockers],
    )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def _create_corrective_wo(
    db: Session,
    issue: Issue,
    issue_number: str,
    req: CreateIssueRequest,
) -> WorkOrder:
    """Create a corrective Work Order linked to the issue (same transaction).

    If estimated_cost > the outlet's approval threshold (get_approval_threshold):
      - WO status = on-hold, requires_approval = True
      - Create ApprovalRequest (type=maintenance) + 2 default steps
      - Link WO.approval_id → the new ApprovalRequest
    """
    from app.services.approval_service import create_approval_with_steps

    # Resolve the asset (optional — issue can exist without an asset, but a
    # supplied assetId must exist: silently dropping it would create a WO that
    # claims to be about an asset it is not linked to).
    asset = None
    if req.assetId:
        # The asset picker falls back to a free-text box when the CMMS module is
        # still empty, so whatever the user typed arrives here. Reject it as a
        # 422 — handing a non-UUID straight to Postgres raises a DataError that
        # escapes as an opaque 500.
        try:
            asset_uuid = uuid.UUID(str(req.assetId))
        except (ValueError, AttributeError, TypeError):
            raise HTTPException(
                status_code=422,
                detail=(
                    f"{req.assetId!r} is not a valid asset id. "
                    "Pick an asset from the list, or register it under "
                    "Assets \u2192 Physical Assets first."
                ),
            )
        asset = db.query(Asset).filter(Asset.id == asset_uuid).first()
        if asset is None:
            # Raised before any commit → the whole issue/WO/approval creation
            # rolls back, leaving no orphan rows.
            raise HTTPException(status_code=404, detail="Asset not found")

    asset_name = asset.name if asset else req.title
    wo_number  = _next_wo_number(db)

    requires_approval = needs_approval(
        req.estimatedCost, get_approval_threshold(db, resolve_outlet_id(db, req.outlet)),
    )
    wo_status = WorkOrderStatusEnum.on_hold.value if requires_approval else "scheduled"

    wo = WorkOrder(
        number=wo_number,
        type="corrective",
        asset_id=asset.id if asset else None,
        asset_name=asset_name,
        outlet=req.outlet,
        outlet_id=resolve_outlet_id(db, req.outlet),
        issue_id=issue.id,
        issue_number=issue_number,
        title=_derived_title("[Korektif] ", req.title),
        description=req.description,
        priority=req.priority,
        status=wo_status,
        assignee=req.assignee or "Unassigned",
        estimated_cost=req.estimatedCost,
        requires_approval=requires_approval,
    )
    db.add(wo)
    db.flush()  # need wo.id before creating approval
    notify_work_order_assigned(db, wo)

    if requires_approval:
        approval = create_approval_with_steps(
            db,
            issue_id=issue.id,
            issue_number=issue_number,
            title=_derived_title("Approval biaya: ", req.title),
            approval_type="maintenance",
            description=req.description,
            requester=req.assignee or "Unassigned",
            outlet=req.outlet,
            outlet_id=resolve_outlet_id(db, req.outlet),
            amount=int(req.estimatedCost) if req.estimatedCost is not None else None,
            flush_only=True,   # stay in caller's transaction
        )
        wo.approval_id = approval.id

    return wo


def create_issue(db: Session, req: CreateIssueRequest, commit: bool = True) -> IssueResponse:
    """Create an Issue and optionally auto-generate a linked Task and/or Approval.

    All three inserts happen in the same database transaction so a failure
    in task/approval creation rolls back the issue too. commit=False leaves the
    commit to the caller (e.g. a QA audit submit raising several Issues at once).
    """
    issue_number = _next_number(db, "ISS", "issue_number_sequences")

    # Determine initial status from assignee (mirrors frontend store logic)
    is_assigned = req.assignee and req.assignee.lower() != "unassigned"
    initial_status = IssueStatusEnum.assigned if is_assigned else IssueStatusEnum.open

    issue = Issue(
        number=issue_number,
        title=req.title,
        description=req.description,
        outlet=req.outlet,
        outlet_id=resolve_outlet_id(db, req.outlet),
        category=req.category,
        priority=req.priority,
        status=initial_status.value,
        assignee=req.assignee or "Unassigned",
        due_date=_parse_date(req.dueDate),
    )
    db.add(issue)
    db.flush()  # get issue.id without committing
    # A Guest Service issue always carries its guest case (Todo-Pilot §8).
    ensure_guest_case(db, issue)

    # Auto-generate Task (FR-6)
    if req.generateTask:
        task_number = _next_number(db, "TSK", "task_number_sequences")
        task_status = IssueStatusEnum.assigned.value if is_assigned else IssueStatusEnum.open.value
        task = Task(
            issue_id=issue.id,
            issue_number=issue_number,
            number=task_number,
            title=_derived_title("Resolve: ", req.title),
            description=req.description,
            status=task_status,
            priority=req.priority,
            assignee=req.assignee or "Unassigned",
            due_date=_parse_date(req.dueDate),
            outlet=req.outlet,
            outlet_id=resolve_outlet_id(db, req.outlet),
        )
        db.add(task)

    # Corrective Work Order first (Tier 1 §1.3). When the estimated cost clears
    # the threshold it creates this issue's ApprovalRequest itself, and
    # approval_requests.issue_id is UNIQUE — so the generic branch below has to
    # know one already exists.
    wo = None
    if req.category == "Maintenance" and req.generateWorkOrder:
        wo = _create_corrective_wo(db, issue, issue_number, req)

    # Auto-generate ApprovalRequest (FR-6). This used to skip Maintenance
    # entirely, which silently dropped the user's "Send to Approval Center"
    # tick whenever no work order had already raised one.
    # Must go through create_approval_with_steps: a bare ApprovalRequest has no
    # steps, so it can never be decided ("No step with order 1").
    if req.generateApproval and (wo is None or wo.approval_id is None):
        from app.services.approval_service import create_approval_with_steps

        create_approval_with_steps(
            db,
            issue_id=issue.id,
            issue_number=issue_number,
            title=req.title,
            approval_type=get_approval_type(req.category),
            description=req.description,
            requester=req.assignee or "Unassigned",
            outlet=req.outlet,
            outlet_id=resolve_outlet_id(db, req.outlet),
            amount=req.approvalAmount,
            flush_only=True,   # stay in caller's transaction
        )

    if commit:
        db.commit()
    db.flush()
    db.refresh(issue)

    notify_issue_created(db, issue_number, req.title, req.outlet, issue.id, outlet_id=issue.outlet_id)
    if commit:
        db.commit()

    return _issue_to_response(issue)


def list_issues(
    db: Session,
    status: Optional[str] = None,
    category: Optional[str] = None,
    outlet: Optional[str] = None,
    user=None,
) -> List[IssueResponse]:
    # Outlet scoping (Tier 4.2b): callers pass the authenticated user.
    query = scoped_query(db, Issue, user) if user is not None else db.query(Issue)
    if status:
        query = query.filter(Issue.status == status)
    if category:
        query = query.filter(Issue.category == category)
    if outlet:
        query = query.filter(Issue.outlet == outlet)
    # Newest first
    issues = query.order_by(Issue.created_at.desc()).all()
    return [_issue_to_response(i) for i in issues]


def get_issue(db: Session, issue_id: str) -> Optional[IssueResponse]:
    issue = db.query(Issue).filter(Issue.id == issue_id).first()
    if not issue:
        return None
    return _issue_to_response(issue)


def _guard_status_change(issue: Issue, old_status: str, new_status: str) -> None:
    """Plain PATCH status rules (Todo-Pilot §1–2). Raises 409/422."""
    if new_status not in {e.value for e in IssueStatusEnum}:
        raise HTTPException(status_code=422, detail=f"Unknown issue status: {new_status!r}")
    if new_status == IssueStatusEnum.cancelled.value:
        raise HTTPException(status_code=409, detail="Use POST /api/issues/{id}/cancel to cancel an Issue.")
    if old_status in ("closed", "cancelled"):
        raise HTTPException(status_code=409, detail=f"A {old_status} Issue cannot change status.")
    if old_status == "resolved" and new_status != "closed":
        raise HTTPException(
            status_code=409,
            detail="Use POST /api/issues/{id}/reopen (with a reason) to reopen a resolved Issue.",
        )
    if new_status in ISSUE_CLOSING:
        check = can_close_issue(issue)
        if not check.can_close:
            raise HTTPException(
                status_code=409,
                detail={"message": check.message(), "blockers": [b.as_dict() for b in check.blockers]},
            )


def update_issue(db: Session, issue_id: str, req: UpdateIssueRequest,
                 actor: str = "system") -> Optional[IssueResponse]:
    issue = db.query(Issue).filter(Issue.id == issue_id).first()
    if not issue:
        return None

    old_status = _value(issue.status)

    if req.status is not None and req.status != old_status:
        _guard_status_change(issue, old_status, req.status)
        issue.status = req.status
    if req.title is not None:
        issue.title = req.title
    if req.description is not None:
        issue.description = req.description
    if req.assignee is not None:
        issue.assignee = req.assignee
    if req.priority is not None:
        issue.priority = req.priority
    if req.dueDate is not None:
        issue.due_date = _parse_date(req.dueDate)

    new_status = issue.status.value if hasattr(issue.status, "value") else str(issue.status)

    if req.status is not None and old_status != new_status:
        write_audit(
            db,
            table_name="issues",
            record_id=str(issue.id),
            action="status_change",
            old_value={"status": old_status, "number": issue.number},
            new_value={"status": new_status, "number": issue.number},
            performed_by=actor,
        )
        notify_issue_status_changed(db, issue.number, issue.title, old_status, new_status, issue.id)
    elif any(v is not None for v in [req.title, req.description, req.assignee, req.priority, req.dueDate]):
        write_audit(
            db,
            table_name="issues",
            record_id=str(issue.id),
            action="update",
            new_value={"number": issue.number, "title": issue.title},
            performed_by=actor,
        )

    db.commit()
    db.refresh(issue)
    return _issue_to_response(issue)


# ---------------------------------------------------------------------------
# Lifecycle: cancel / reopen / revise after rejection (Todo-Pilot §2)
# ---------------------------------------------------------------------------

def _audit_status(db: Session, issue: Issue, old_status: str, new_status: str,
                  actor: str, **extra) -> None:
    write_audit(
        db,
        table_name="issues",
        record_id=str(issue.id),
        action="status_change",
        old_value={"status": old_status, "number": issue.number},
        new_value={"status": new_status, "number": issue.number, **extra},
        performed_by=actor,
    )
    notify_issue_status_changed(db, issue.number, issue.title, old_status, new_status, issue.id)


def cancel_issue(db: Session, issue: Issue, reason: Optional[str], actor: str) -> IssueResponse:
    """Cancel an Issue and everything still open under it, in one transaction.

    - Tasks not yet terminal        → cancelled
    - Work Orders not yet terminal  → cancelled (via the WO state machine)
    - a pending Approval            → rejected, with a system comment
    """
    from app.services.approval_service import cancel_pending_approval
    from app.services.work_order_service import transition_work_order

    old_status = _value(issue.status)
    if old_status in ISSUE_FINAL:
        raise HTTPException(status_code=409, detail=f"Issue is already {old_status}.")

    note = f"Dibatalkan otomatis: Issue {issue.number} dibatalkan"
    if reason:
        note += f" — {reason}"

    for task in issue.tasks:
        task_old = _value(task.status)
        if task_old in TASK_TERMINAL:
            continue
        task.status = TaskStatusEnum.cancelled.value
        write_audit(db, table_name="tasks", record_id=str(task.id), action="status_change",
                    old_value={"status": task_old, "number": task.number},
                    new_value={"status": "cancelled", "number": task.number, "reason": "issue_cancelled"},
                    performed_by=actor)

    if issue.approval is not None and cancel_pending_approval(db, issue.approval, note):
        write_audit(db, table_name="approval_requests", record_id=str(issue.approval.id),
                    action="status_change",
                    old_value={"status": "pending", "number": issue.approval.number},
                    new_value={"status": "rejected", "number": issue.approval.number,
                               "reason": "issue_cancelled"},
                    performed_by=actor)

    for wo in issue.work_orders:
        if _value(wo.status) in WORK_ORDER_TERMINAL:
            continue
        transition_work_order(db, wo, WorkOrderStatusEnum.cancelled, commit=False)

    issue.status = IssueStatusEnum.cancelled.value
    _audit_status(db, issue, old_status, "cancelled", actor, reason=reason or "")

    db.commit()
    db.refresh(issue)
    return _issue_to_response(issue)


def reopen_issue(db: Session, issue: Issue, reason: str, actor: str) -> IssueResponse:
    """resolved → in-progress. The reason is mandatory and kept in the audit log."""
    old_status = _value(issue.status)
    if old_status != IssueStatusEnum.resolved.value:
        raise HTTPException(status_code=409,
                            detail=f"Only a resolved Issue can be reopened (this one is {old_status}).")
    issue.status = IssueStatusEnum.in_progress.value
    _audit_status(db, issue, old_status, issue.status, actor, reason=reason, event="reopen")
    db.commit()
    db.refresh(issue)
    return _issue_to_response(issue)


def revise_approval(db: Session, issue: Issue, amount: int, reason: Optional[str],
                    actor: str) -> IssueResponse:
    """After a rejection (Issue `waiting`), send a revised cost back through approval.

    Reuses the Issue's ApprovalRequest (issue_id is UNIQUE) with a fresh step
    chain. The corrective WO the rejection cancelled was never started, so it is
    put back on hold with the new estimate instead of creating a second WO.
    """
    from app.services.approval_service import restart_approval

    old_status = _value(issue.status)
    approval = issue.approval
    if old_status != IssueStatusEnum.waiting.value or approval is None \
            or _value(approval.status) != "rejected":
        raise HTTPException(
            status_code=409,
            detail="Only a waiting Issue whose approval was rejected can be revised.",
        )

    old_amount = approval.amount
    restart_approval(db, approval, amount)
    write_audit(db, table_name="approval_requests", record_id=str(approval.id), action="revise",
                old_value={"status": "rejected", "amount": old_amount, "number": approval.number},
                new_value={"status": "pending", "amount": amount, "number": approval.number,
                           "reason": reason or ""},
                performed_by=actor)

    for wo in issue.work_orders:
        if wo.approval_id == approval.id and _value(wo.status) == WorkOrderStatusEnum.cancelled.value:
            # Deliberately outside the WO state machine (cancelled is terminal
            # there): this WO was cancelled by the rejection before any work.
            wo.status = WorkOrderStatusEnum.on_hold.value
            wo.estimated_cost = amount
            write_audit(db, table_name="work_orders", record_id=str(wo.id), action="status_change",
                        old_value={"status": "cancelled"},
                        new_value={"status": "on-hold", "number": wo.number,
                                   "reason": "approval_revised", "estimatedCost": amount},
                        performed_by=actor)

    issue.status = IssueStatusEnum.in_progress.value
    _audit_status(db, issue, old_status, issue.status, actor, reason="approval_revised")
    db.commit()
    db.refresh(issue)
    return _issue_to_response(issue)


# Closure rules and the Task → Issue roll-up live in issue_closure_service;
# re-exported here for existing callers/tests.
from app.services.issue_closure_service import (  # noqa: E402,F401
    derive_issue_status_from_tasks,
    rollup_issue_from_tasks,
)
