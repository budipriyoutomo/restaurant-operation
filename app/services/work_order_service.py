"""Work Order service — state machine, cost logic, asset-status sync.

Pure functions (no DB):
  can_transition, compute_total_cost, compute_downtime_hours

DB functions:
  transition_work_order   — validates + applies transition + asset sync
  update_cost             — writes cost fields
  add_checklist_item      — appends a checklist row
  toggle_checklist_item   — flips is_done
  add_attachment          — appends an attachment row
  wo_to_response          — ORM → WorkOrderResponse (lightweight list view)
  wo_to_detail_response   — ORM → WorkOrderDetailResponse (checklist + attachments)
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy.orm import Session

from app.models.asset import (
    Asset,
    WorkOrder,
    WorkOrderAttachment,
    WorkOrderChecklistItem,
)
from app.models.enums import AssetStatusEnum, WorkOrderStatusEnum
from app.schemas.asset import (
    ChecklistItemCreate,
    ChecklistItemResponse,
    ChecklistItemUpdate,
    WorkOrderAttachmentCreate,
    WorkOrderAttachmentResponse,
    WorkOrderCostUpdate,
    WorkOrderDetailResponse,
    WorkOrderResponse,
)
from app.services.audit_service import write_audit

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

APPROVAL_THRESHOLD: float = 1_000_000  # Rp 1 juta — trigger approval above this

# Work orders whose status counts as "active" for the purpose of asset sync.
_ACTIVE_STATUSES = {"scheduled", "in-progress", "on-hold"}


# ---------------------------------------------------------------------------
# Domain exceptions
# ---------------------------------------------------------------------------

class InvalidTransitionError(ValueError):
    """Raised when a requested WO status transition is not in the state machine."""


# ---------------------------------------------------------------------------
# State machine (pure — no DB)
# ---------------------------------------------------------------------------

_TRANSITIONS: dict[str, set[str]] = {
    "scheduled":   {"in-progress", "cancelled", "on-hold"},
    "on-hold":     {"in-progress", "cancelled"},
    "in-progress": {"completed", "on-hold", "cancelled"},
    "completed":   set(),
    "cancelled":   set(),
}


def can_transition(current: WorkOrderStatusEnum, target: WorkOrderStatusEnum) -> bool:
    """Return True if the transition is allowed, raise InvalidTransitionError otherwise."""
    current_val = current.value if hasattr(current, "value") else str(current)
    target_val  = target.value  if hasattr(target, "value")  else str(target)
    allowed = _TRANSITIONS.get(current_val, set())
    if target_val not in allowed:
        raise InvalidTransitionError(
            f"Cannot transition work order from '{current_val}' to '{target_val}'."
        )
    return True


# ---------------------------------------------------------------------------
# Cost & downtime helpers (pure — no DB)
# ---------------------------------------------------------------------------

def compute_total_cost(labor_cost: float, parts_cost: float) -> float:
    return float(labor_cost or 0) + float(parts_cost or 0)


def compute_downtime_hours(
    start: Optional[datetime],
    end: Optional[datetime],
) -> float:
    if start is None or end is None:
        return 0.0
    delta = end - start
    return delta.total_seconds() / 3600


# ---------------------------------------------------------------------------
# ORM → schema helpers
# ---------------------------------------------------------------------------

def wo_to_response(wo: WorkOrder) -> WorkOrderResponse:
    labor  = float(wo.labor_cost  or 0)
    parts  = float(wo.parts_cost  or 0)
    return WorkOrderResponse(
        id=str(wo.id),
        number=wo.number,
        type=wo.type.value if hasattr(wo.type, "value") else str(wo.type),
        assetId=str(wo.asset_id) if wo.asset_id else None,
        assetName=wo.asset_name,
        outlet=wo.outlet,
        issueId=str(wo.issue_id) if wo.issue_id else None,
        issueNumber=wo.issue_number,
        title=wo.title,
        description=wo.description or "",
        priority=wo.priority.value if hasattr(wo.priority, "value") else str(wo.priority),
        status=wo.status.value if hasattr(wo.status, "value") else str(wo.status),
        assignee=wo.assignee or "Unassigned",
        scheduledDate=wo.scheduled_date.isoformat() if wo.scheduled_date else None,
        completedDate=wo.completed_date.isoformat() if wo.completed_date else None,
        createdAt=wo.created_at.isoformat() if wo.created_at else "",
        downtimeStart=wo.downtime_start.isoformat() if wo.downtime_start else None,
        downtimeEnd=wo.downtime_end.isoformat() if wo.downtime_end else None,
        laborHours=float(wo.labor_hours) if wo.labor_hours is not None else None,
        laborCost=labor,
        partsCost=parts,
        totalCost=compute_total_cost(labor, parts),
        estimatedCost=float(wo.estimated_cost) if wo.estimated_cost is not None else None,
        requiresApproval=bool(wo.requires_approval),
        approvalId=str(wo.approval_id) if wo.approval_id else None,
    )


def wo_to_detail_response(wo: WorkOrder) -> WorkOrderDetailResponse:
    base = wo_to_response(wo)
    return WorkOrderDetailResponse(
        **base.model_dump(),
        checklistItems=[
            ChecklistItemResponse(
                id=str(item.id),
                workOrderId=str(item.work_order_id),
                title=item.title,
                isDone=bool(item.is_done),
                doneBy=str(item.done_by) if item.done_by else None,
                doneAt=item.done_at.isoformat() if item.done_at else None,
                orderIndex=item.order_index,
            )
            for item in (wo.checklist_items or [])
        ],
        attachments=[
            WorkOrderAttachmentResponse(
                id=str(att.id),
                workOrderId=str(att.work_order_id),
                fileUrl=att.file_url,
                caption=att.caption,
                uploadedBy=str(att.uploaded_by),
                createdAt=att.created_at.isoformat() if att.created_at else "",
            )
            for att in (wo.attachments or [])
        ],
    )


# ---------------------------------------------------------------------------
# DB: transition
# ---------------------------------------------------------------------------

def transition_work_order(
    db: Session,
    wo: WorkOrder,
    target_status: WorkOrderStatusEnum,
    actor_user_id: Optional[str] = None,
) -> WorkOrder:
    """Validate and apply a status transition with all side effects.

    Side effects:
    - in-progress (corrective): set downtime_start if not already set
    - completed: set downtime_end, compute total_cost
    - Asset status sync (corrective WOs only)
    """
    old_status = wo.status.value if hasattr(wo.status, "value") else str(wo.status)
    target_val = target_status.value if hasattr(target_status, "value") else str(target_status)

    can_transition(wo.status, target_status)   # raises InvalidTransitionError if illegal

    wo.status = target_val
    now = datetime.now(timezone.utc)

    # Downtime tracking
    wo_type = wo.type.value if hasattr(wo.type, "value") else str(wo.type)
    if target_val == "in-progress" and wo_type == "corrective":
        if wo.downtime_start is None:
            wo.downtime_start = now

    if target_val == "completed":
        wo.downtime_end = now
        wo.labor_cost  = wo.labor_cost  or 0
        wo.parts_cost  = wo.parts_cost  or 0
        # total_cost is a computed field in the schema — not stored separately

    # Asset status sync
    if wo.asset_id and wo_type == "corrective":
        _sync_asset_status(db, wo)

    write_audit(
        db,
        table_name="work_orders",
        record_id=str(wo.id),
        action="status_change",
        old_value={"status": old_status},
        new_value={"status": target_val, "number": wo.number},
    )

    db.commit()
    db.refresh(wo)
    return wo


def _sync_asset_status(db: Session, wo: WorkOrder) -> None:
    """Update the parent asset's status based on WO's new status."""
    asset = db.query(Asset).filter(Asset.id == wo.asset_id).first()
    if not asset:
        return

    wo_status = wo.status.value if hasattr(wo.status, "value") else str(wo.status)

    if wo_status == "in-progress":
        asset.status = AssetStatusEnum.maintenance.value
        return

    # For completed or cancelled: restore operational if no other active WOs
    other_active = (
        db.query(WorkOrder)
        .filter(
            WorkOrder.asset_id == wo.asset_id,
            WorkOrder.id != wo.id,
            WorkOrder.status.in_(_ACTIVE_STATUSES),
        )
        .count()
    )
    if other_active == 0:
        asset.status = AssetStatusEnum.operational.value


# ---------------------------------------------------------------------------
# DB: cost update
# ---------------------------------------------------------------------------

def update_wo_cost(db: Session, wo: WorkOrder, req: WorkOrderCostUpdate) -> WorkOrder:
    if req.laborHours is not None:
        wo.labor_hours = req.laborHours
    if req.laborCost is not None:
        wo.labor_cost = req.laborCost
    if req.partsCost is not None:
        wo.parts_cost = req.partsCost

    write_audit(
        db,
        table_name="work_orders",
        record_id=str(wo.id),
        action="cost_update",
        new_value={
            "number": wo.number,
            "laborCost": float(wo.labor_cost or 0),
            "partsCost": float(wo.parts_cost or 0),
        },
    )
    db.commit()
    db.refresh(wo)
    return wo


# ---------------------------------------------------------------------------
# DB: checklist
# ---------------------------------------------------------------------------

def add_checklist_item(
    db: Session, wo: WorkOrder, req: ChecklistItemCreate
) -> WorkOrderChecklistItem:
    item = WorkOrderChecklistItem(
        work_order_id=wo.id,
        title=req.title,
        order_index=req.orderIndex,
    )
    db.add(item)
    db.commit()
    db.refresh(item)
    return item


def toggle_checklist_item(
    db: Session,
    item: WorkOrderChecklistItem,
    req: ChecklistItemUpdate,
    actor_user_id: Optional[str] = None,
) -> WorkOrderChecklistItem:
    item.is_done = req.isDone
    if req.isDone:
        item.done_at = datetime.now(timezone.utc)
        if actor_user_id:
            try:
                item.done_by = uuid.UUID(actor_user_id)
            except ValueError:
                pass
    else:
        item.done_at = None
        item.done_by = None

    db.commit()
    db.refresh(item)
    return item


# ---------------------------------------------------------------------------
# DB: attachments
# ---------------------------------------------------------------------------

def add_attachment(
    db: Session,
    wo: WorkOrder,
    req: WorkOrderAttachmentCreate,
    uploader_user_id: str,
) -> WorkOrderAttachment:
    att = WorkOrderAttachment(
        work_order_id=wo.id,
        file_url=req.fileUrl,
        caption=req.caption,
        uploaded_by=uuid.UUID(uploader_user_id),
    )
    db.add(att)
    db.commit()
    db.refresh(att)
    return att
