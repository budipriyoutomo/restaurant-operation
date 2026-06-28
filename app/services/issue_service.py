"""Issue service — the single entry point for all Issue creation and retrieval.

This is where the auto-generation logic lives: creating an Issue here
can automatically produce a Task and/or an ApprovalRequest, fully linked.
"""

from datetime import date, datetime
from typing import List, Optional

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.category_defaults import get_approval_type
from app.models.approval import ApprovalRequest, ApprovalNumberSequence
from app.models.asset import Asset, WorkOrder, WorkOrderNumberSequence
from app.models.enums import IssueStatusEnum, PriorityEnum, WorkOrderStatusEnum
from app.models.issue import Issue, IssueNumberSequence
from app.models.task import Task, TaskNumberSequence
from app.schemas.issue import CreateIssueRequest, IssueResponse, UpdateIssueRequest
from app.services.audit_service import write_audit
from app.services.notification_service import notify_issue_created, notify_issue_status_changed
from app.services.work_order_service import APPROVAL_THRESHOLD


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _next_number(db: Session, prefix: str, table: str) -> str:
    """Atomically increment the per-year sequence and return a formatted number.

    Uses an INSERT … ON CONFLICT … DO UPDATE pattern inside the caller's
    transaction so concurrent creates never produce the same number.
    """
    year = datetime.now().year
    result = db.execute(
        text(
            f"""
            INSERT INTO {table} (year, last_seq)
            VALUES (:year, 1)
            ON CONFLICT (year) DO UPDATE
              SET last_seq = {table}.last_seq + 1
            RETURNING last_seq
            """
        ),
        {"year": year},
    )
    seq = result.scalar_one()
    return f"{prefix}-{year}-{seq:05d}"


def _compute_sla_breach(due_date, status_value: str) -> bool:
    if due_date is None:
        return False
    if status_value in ("resolved", "closed"):
        return False
    today = date.today()
    return due_date < today


def _parse_date(date_str: Optional[str]):
    """Parse an ISO date string ('2026-06-25') or return None."""
    if not date_str:
        return None
    try:
        return date.fromisoformat(date_str)
    except ValueError:
        return None


def _next_wo_number(db: Session) -> str:
    year = datetime.now().year
    result = db.execute(
        text("""
            INSERT INTO work_order_number_sequences (year, last_seq)
            VALUES (:year, 1)
            ON CONFLICT (year) DO UPDATE
              SET last_seq = work_order_number_sequences.last_seq + 1
            RETURNING last_seq
        """),
        {"year": year},
    )
    seq = result.scalar_one()
    return f"WO-{year}-{seq:05d}"


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

    If estimated_cost > APPROVAL_THRESHOLD:
      - WO status = on-hold, requires_approval = True
      - Create ApprovalRequest (type=maintenance) + 2 default steps
      - Link WO.approval_id → the new ApprovalRequest
    """
    from app.services.approval_service import create_approval_with_steps

    # Resolve the asset (optional — issue can exist without an asset)
    asset = None
    if req.assetId:
        asset = db.query(Asset).filter(Asset.id == req.assetId).first()

    asset_name = asset.name if asset else req.title
    wo_number  = _next_wo_number(db)

    needs_approval = (
        req.estimatedCost is not None and req.estimatedCost > APPROVAL_THRESHOLD
    )
    wo_status = WorkOrderStatusEnum.on_hold.value if needs_approval else "scheduled"

    wo = WorkOrder(
        number=wo_number,
        type="corrective",
        asset_id=asset.id if asset else None,
        asset_name=asset_name,
        outlet=req.outlet,
        issue_id=issue.id,
        issue_number=issue_number,
        title=f"[Korektif] {req.title}",
        description=req.description,
        priority=req.priority,
        status=wo_status,
        assignee=req.assignee or "Unassigned",
        estimated_cost=req.estimatedCost,
        requires_approval=needs_approval,
    )
    db.add(wo)
    db.flush()  # need wo.id before creating approval

    if needs_approval:
        approval = create_approval_with_steps(
            db,
            issue_id=issue.id,
            issue_number=issue_number,
            title=f"Approval biaya: {req.title}",
            approval_type="maintenance",
            description=req.description,
            requester=req.assignee or "Unassigned",
            outlet=req.outlet,
            amount=str(req.estimatedCost),
            flush_only=True,   # stay in caller's transaction
        )
        wo.approval_id = approval.id

    return wo


def create_issue(db: Session, req: CreateIssueRequest) -> IssueResponse:
    """Create an Issue and optionally auto-generate a linked Task and/or Approval.

    All three inserts happen in the same database transaction so a failure
    in task/approval creation rolls back the issue too.
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
        category=req.category,
        priority=req.priority,
        status=initial_status.value,
        assignee=req.assignee or "Unassigned",
        due_date=_parse_date(req.dueDate),
    )
    db.add(issue)
    db.flush()  # get issue.id without committing

    # Auto-generate Task (FR-6)
    if req.generateTask:
        task_number = _next_number(db, "TSK", "task_number_sequences")
        task_status = IssueStatusEnum.assigned.value if is_assigned else IssueStatusEnum.open.value
        task = Task(
            issue_id=issue.id,
            issue_number=issue_number,
            number=task_number,
            title=f"Resolve: {req.title}",
            description=req.description,
            status=task_status,
            priority=req.priority,
            assignee=req.assignee or "Unassigned",
            due_date=_parse_date(req.dueDate),
            outlet=req.outlet,
        )
        db.add(task)

    # Auto-generate ApprovalRequest (FR-6, non-Maintenance categories)
    if req.generateApproval and req.category != "Maintenance":
        approval_number = _next_number(db, "APR", "approval_number_sequences")
        approval_type = get_approval_type(req.category)
        approval = ApprovalRequest(
            issue_id=issue.id,
            issue_number=issue_number,
            number=approval_number,
            title=req.title,
            type=approval_type,
            description=req.description,
            requester=req.assignee or "Unassigned",
            outlet=req.outlet,
            requested_date=date.today(),
            amount=req.approvalAmount,
            status="pending",
        )
        db.add(approval)

    # Auto-generate corrective Work Order for Maintenance issues (Tier 1 §1.3)
    if req.category == "Maintenance" and req.generateWorkOrder:
        _create_corrective_wo(db, issue, issue_number, req)

    db.commit()
    db.refresh(issue)

    notify_issue_created(db, issue_number, req.title, req.outlet, issue.id)
    db.commit()

    return _issue_to_response(issue)


def list_issues(
    db: Session,
    status: Optional[str] = None,
    category: Optional[str] = None,
    outlet: Optional[str] = None,
) -> List[IssueResponse]:
    query = db.query(Issue)
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


def update_issue(db: Session, issue_id: str, req: UpdateIssueRequest) -> Optional[IssueResponse]:
    issue = db.query(Issue).filter(Issue.id == issue_id).first()
    if not issue:
        return None

    old_status = issue.status.value if hasattr(issue.status, "value") else str(issue.status)

    if req.status is not None:
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
        )
        notify_issue_status_changed(db, issue.number, issue.title, old_status, new_status, issue.id)
    elif any(v is not None for v in [req.title, req.description, req.assignee, req.priority, req.dueDate]):
        write_audit(
            db,
            table_name="issues",
            record_id=str(issue.id),
            action="update",
            new_value={"number": issue.number, "title": issue.title},
        )

    db.commit()
    db.refresh(issue)
    return _issue_to_response(issue)
