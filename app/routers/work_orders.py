import uuid
from datetime import datetime
from typing import List, Optional

from fastapi import APIRouter, Depends, File, Form, Header, HTTPException, Query, Request, UploadFile
from fastapi.responses import StreamingResponse
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.asset import Asset, WorkOrder, WorkOrderAttachment, WorkOrderChecklistItem, WorkOrderNumberSequence
from app.services import idempotency_service, storage_service
from app.models.enums import WorkOrderStatusEnum
from app.schemas.asset import (
    ChecklistItemCreate,
    ChecklistItemResponse,
    ChecklistItemUpdate,
    CreateWorkOrderRequest,
    UpdateWorkOrderRequest,
    WorkOrderAttachmentCreate,
    WorkOrderAttachmentResponse,
    WorkOrderCostUpdate,
    WorkOrderDetailResponse,
    WorkOrderResponse,
    WorkOrderTransitionRequest,
)
from app.models.part import Part
from app.models.vendor import Vendor
from app.schemas.asset import AssignVendorRequest
from app.schemas.part import ConsumePartRequest, WorkOrderPartResponse
from app.services import parts_service as parts_svc
from app.services import vendor_maintenance_service as vendor_svc
from app.services import work_order_service as svc
from app.services.audit_service import write_audit
from app.services.outlet_scope_service import assert_can_access, assert_can_write_outlet, resolve_outlet_id, scoped_query
from app.services.auth_service import UserResponse, get_current_user, require_roles
from app.services.work_order_service import InvalidTransitionError

router = APIRouter(prefix="/api/work-orders", tags=["cmms"])


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

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


def _parse_date(date_str: Optional[str]):
    if not date_str:
        return None
    try:
        from datetime import date
        return date.fromisoformat(date_str)
    except ValueError:
        return None


def _get_wo_or_404(db: Session, wo_id: str, user=None) -> WorkOrder:
    """Fetch a work order, 404-ing both when it is missing and when it belongs to
    an outlet the caller cannot see (same status code on purpose — a 403 would
    confirm the record exists)."""
    wo = db.query(WorkOrder).filter(WorkOrder.id == wo_id).first()
    if not wo:
        raise HTTPException(status_code=404, detail="Work order not found")
    if user is not None:
        assert_can_access(db, wo, user)
    return wo


# ---------------------------------------------------------------------------
# List / Create / Get / Update / Delete  (existing, updated to use service)
# ---------------------------------------------------------------------------

@router.get("", response_model=List[WorkOrderResponse])
def list_work_orders(
    asset_id: Optional[str] = Query(None),
    issue_id: Optional[str] = Query(None),
    status: Optional[str] = Query(None),
    outlet: Optional[str] = Query(None),
    db: Session = Depends(get_db),
    current_user: UserResponse = Depends(get_current_user),
):
    query = scoped_query(db, WorkOrder, current_user)
    if asset_id:
        query = query.filter(WorkOrder.asset_id == asset_id)
    if issue_id:
        query = query.filter(WorkOrder.issue_id == issue_id)
    if status:
        query = query.filter(WorkOrder.status == status)
    if outlet:
        query = query.filter(WorkOrder.outlet == outlet)
    return [svc.wo_to_response(wo) for wo in query.order_by(WorkOrder.created_at.desc()).all()]


@router.post("", response_model=WorkOrderResponse, status_code=201)
def create_work_order(
    req: CreateWorkOrderRequest,
    db: Session = Depends(get_db),
    current_user: UserResponse = Depends(require_roles("manager", "admin")),
):
    asset = db.query(Asset).filter(Asset.id == req.assetId).first()
    if not asset:
        raise HTTPException(status_code=404, detail="Asset not found")

    wo_outlet_id = resolve_outlet_id(db, asset.outlet)
    assert_can_write_outlet(db, wo_outlet_id, current_user)
    number = _next_wo_number(db)
    wo = WorkOrder(
        number=number,
        type=req.type,
        asset_id=asset.id,
        asset_name=asset.name,
        outlet=asset.outlet,
        outlet_id=wo_outlet_id,
        issue_id=req.issueId or None,
        issue_number=req.issueNumber,
        title=req.title,
        description=req.description,
        priority=req.priority,
        assignee=req.assignee,
        scheduled_date=_parse_date(req.scheduledDate),
        estimated_cost=req.estimatedCost,
    )
    db.add(wo)
    db.flush()
    write_audit(db, table_name="work_orders", record_id=str(wo.id), action="create",
                new_value={"number": wo.number, "asset": wo.asset_name, "outlet": wo.outlet})
    db.commit()
    db.refresh(wo)
    return svc.wo_to_response(wo)


@router.get("/{wo_id}", response_model=WorkOrderDetailResponse)
def get_work_order(
    wo_id: str,
    db: Session = Depends(get_db),
    current_user: UserResponse = Depends(get_current_user),
):
    """Return full work order detail including checklist and attachments."""
    wo = _get_wo_or_404(db, wo_id, current_user)
    return svc.wo_to_detail_response(wo)


@router.patch("/{wo_id}", response_model=WorkOrderResponse)
def update_work_order(
    wo_id: str,
    req: UpdateWorkOrderRequest,
    db: Session = Depends(get_db),
    current_user: UserResponse = Depends(require_roles("manager", "admin")),
):
    wo = _get_wo_or_404(db, wo_id, current_user)
    old_status = wo.status.value if hasattr(wo.status, "value") else str(wo.status)

    if req.status is not None:
        wo.status = req.status
    if req.assignee is not None:
        wo.assignee = req.assignee
    if req.priority is not None:
        wo.priority = req.priority
    if req.scheduledDate is not None:
        wo.scheduled_date = _parse_date(req.scheduledDate)
    if req.completedDate is not None:
        wo.completed_date = _parse_date(req.completedDate)

    new_status = wo.status.value if hasattr(wo.status, "value") else str(wo.status)
    write_audit(db, table_name="work_orders", record_id=str(wo.id), action="update",
                old_value={"status": old_status},
                new_value={"status": new_status, "number": wo.number})
    db.commit()
    db.refresh(wo)
    return svc.wo_to_response(wo)


@router.delete("/{wo_id}", status_code=204)
def delete_work_order(
    wo_id: str,
    db: Session = Depends(get_db),
    current_user: UserResponse = Depends(require_roles("manager", "admin")),
):
    wo = _get_wo_or_404(db, wo_id, current_user)
    write_audit(db, table_name="work_orders", record_id=str(wo.id), action="delete",
                old_value={"number": wo.number, "asset": wo.asset_name})
    db.delete(wo)
    db.commit()


# ---------------------------------------------------------------------------
# PATCH /{wo_id}/transition — state machine transition (manager+)
# ---------------------------------------------------------------------------

@router.patch("/{wo_id}/transition", response_model=WorkOrderDetailResponse)
def transition_work_order(
    wo_id: str,
    req: WorkOrderTransitionRequest,
    db: Session = Depends(get_db),
    current_user: UserResponse = Depends(require_roles("manager", "admin")),
):
    """Transition a work order to a new status via the explicit state machine.

    Legal transitions:
      scheduled   → in-progress | cancelled | on-hold
      on-hold     → in-progress | cancelled
      in-progress → completed   | on-hold   | cancelled
    Any other transition returns 409.
    """
    wo = _get_wo_or_404(db, wo_id, current_user)
    try:
        target = WorkOrderStatusEnum(req.targetStatus)
    except ValueError:
        raise HTTPException(status_code=422, detail=f"Unknown status: {req.targetStatus!r}")

    try:
        svc.transition_work_order(db, wo, target, actor_user_id=current_user.id)
    except InvalidTransitionError as exc:
        raise HTTPException(status_code=409, detail=str(exc))

    db.refresh(wo)
    return svc.wo_to_detail_response(wo)


# ---------------------------------------------------------------------------
# POST /{wo_id}/checklist — add checklist item
# PATCH /{wo_id}/checklist/{item_id} — toggle done
# ---------------------------------------------------------------------------

@router.post("/{wo_id}/checklist", response_model=ChecklistItemResponse, status_code=201)
def add_checklist_item(
    wo_id: str,
    req: ChecklistItemCreate,
    request: Request,
    db: Session = Depends(get_db),
    current_user: UserResponse = Depends(require_roles("manager", "admin")),
    idempotency_key: Optional[str] = Header(None),
):
    cached = idempotency_service.get_cached(db, idempotency_key, current_user.id, request)
    if cached is not None:
        return cached
    wo = _get_wo_or_404(db, wo_id, current_user)
    item = svc.add_checklist_item(db, wo, req)
    resp = ChecklistItemResponse(
        id=str(item.id),
        workOrderId=str(item.work_order_id),
        title=item.title,
        isDone=bool(item.is_done),
        doneBy=str(item.done_by) if item.done_by else None,
        doneAt=item.done_at.isoformat() if item.done_at else None,
        orderIndex=item.order_index,
    )
    idempotency_service.store(db, idempotency_key, current_user.id, request, 201, resp)
    return resp


@router.patch("/{wo_id}/checklist/{item_id}", response_model=ChecklistItemResponse)
def toggle_checklist_item(
    wo_id: str,
    item_id: str,
    req: ChecklistItemUpdate,
    db: Session = Depends(get_db),
    current_user: UserResponse = Depends(get_current_user),
):
    """Toggle a checklist item done/undone. Any authenticated user can check off items."""
    _get_wo_or_404(db, wo_id, current_user)   # validates WO exists
    item = db.query(WorkOrderChecklistItem).filter(
        WorkOrderChecklistItem.id == item_id,
        WorkOrderChecklistItem.work_order_id == wo_id,
    ).first()
    if not item:
        raise HTTPException(status_code=404, detail="Checklist item not found")

    item = svc.toggle_checklist_item(db, item, req, actor_user_id=current_user.id)
    return ChecklistItemResponse(
        id=str(item.id),
        workOrderId=str(item.work_order_id),
        title=item.title,
        isDone=bool(item.is_done),
        doneBy=str(item.done_by) if item.done_by else None,
        doneAt=item.done_at.isoformat() if item.done_at else None,
        orderIndex=item.order_index,
    )


# ---------------------------------------------------------------------------
# PATCH /{wo_id}/cost — update labor/parts cost (manager+)
# ---------------------------------------------------------------------------

@router.patch("/{wo_id}/cost", response_model=WorkOrderDetailResponse)
def update_cost(
    wo_id: str,
    req: WorkOrderCostUpdate,
    db: Session = Depends(get_db),
    current_user: UserResponse = Depends(require_roles("manager", "admin")),
):
    wo = _get_wo_or_404(db, wo_id, current_user)
    wo = svc.update_wo_cost(db, wo, req)
    return svc.wo_to_detail_response(wo)


# ---------------------------------------------------------------------------
# POST /{wo_id}/attachments — add an attachment (any authenticated user)
# ---------------------------------------------------------------------------

@router.post("/{wo_id}/attachments", response_model=WorkOrderAttachmentResponse, status_code=201)
def add_attachment(
    wo_id: str,
    req: WorkOrderAttachmentCreate,
    db: Session = Depends(get_db),
    current_user: UserResponse = Depends(get_current_user),
):
    """Attach an external URL to a work order (kept for documents / links).
    For technician photos use POST /{wo_id}/attachments/upload."""
    wo = _get_wo_or_404(db, wo_id, current_user)
    att = svc.add_attachment(db, wo, req, uploader_user_id=current_user.id)
    return svc.attachment_to_response(att)


@router.post("/{wo_id}/attachments/upload", response_model=WorkOrderAttachmentResponse, status_code=201)
def upload_attachment(
    wo_id: str,
    request: Request,
    file: UploadFile = File(...),
    caption: Optional[str] = Form(None),
    db: Session = Depends(get_db),
    current_user: UserResponse = Depends(get_current_user),
    idempotency_key: Optional[str] = Header(None),
):
    """Upload a real photo taken in the field (Tier 5.1).

    The image is validated, EXIF-stripped (removes embedded GPS = employee
    location), thumbnailed, and stored by the app. Served back via the file
    route, not a caller-supplied URL.
    """
    # A retried offline upload must not create a duplicate attachment.
    cached = idempotency_service.get_cached(db, idempotency_key, current_user.id, request)
    if cached is not None:
        return cached
    wo = _get_wo_or_404(db, wo_id, current_user)
    raw = file.file.read()
    stored = storage_service.save_image(raw, file.content_type or "")
    att = WorkOrderAttachment(
        work_order_id=wo.id,
        uploaded_by=uuid.UUID(current_user.id) if isinstance(current_user.id, str) else current_user.id,
        caption=caption,
        storage_key=stored.storage_key,
        thumbnail_key=stored.thumbnail_key,
        mime_type=stored.mime_type,
        size_bytes=stored.size_bytes,
    )
    db.add(att)
    write_audit(db, table_name="work_order_attachments", record_id=str(wo.id), action="upload",
                new_value={"workOrder": wo.number, "mime": stored.mime_type, "bytes": stored.size_bytes})
    db.commit()
    db.refresh(att)
    resp = svc.attachment_to_response(att)
    idempotency_service.store(db, idempotency_key, current_user.id, request, 201, resp)
    return resp


def _get_attachment_or_404(db: Session, wo_id: str, att_id: str, user) -> WorkOrderAttachment:
    _get_wo_or_404(db, wo_id, user)   # enforces outlet scoping on the parent WO
    att = db.query(WorkOrderAttachment).filter(
        WorkOrderAttachment.id == att_id,
        WorkOrderAttachment.work_order_id == wo_id,
    ).first()
    if not att:
        raise HTTPException(status_code=404, detail="Attachment not found")
    return att


@router.get("/{wo_id}/attachments/{att_id}/file")
def serve_attachment(
    wo_id: str,
    att_id: str,
    db: Session = Depends(get_db),
    current_user: UserResponse = Depends(get_current_user),
):
    att = _get_attachment_or_404(db, wo_id, att_id, current_user)
    if not att.storage_key:
        raise HTTPException(status_code=404, detail="No stored file for this attachment")
    return StreamingResponse(
        storage_service.open_stream(att.storage_key),
        media_type=att.mime_type or "application/octet-stream",
    )


@router.get("/{wo_id}/attachments/{att_id}/thumbnail")
def serve_thumbnail(
    wo_id: str,
    att_id: str,
    db: Session = Depends(get_db),
    current_user: UserResponse = Depends(get_current_user),
):
    att = _get_attachment_or_404(db, wo_id, att_id, current_user)
    key = att.thumbnail_key or att.storage_key
    if not key:
        raise HTTPException(status_code=404, detail="No thumbnail for this attachment")
    return StreamingResponse(
        storage_service.open_stream(key),
        media_type=att.mime_type or "application/octet-stream",
    )


# ---------------------------------------------------------------------------
# Spare parts consumption (Tier 3)
# GET  /{wo_id}/parts — list parts consumed by this WO
# POST /{wo_id}/parts — consume a part (decrements stock, updates parts_cost)
# ---------------------------------------------------------------------------

@router.get("/{wo_id}/parts", response_model=List[WorkOrderPartResponse])
def list_wo_parts(
    wo_id: str,
    db: Session = Depends(get_db),
    current_user: UserResponse = Depends(get_current_user),
):
    wo = _get_wo_or_404(db, wo_id, current_user)
    return [parts_svc.wo_part_to_response(wp) for wp in (wo.parts_used or [])]


@router.post("/{wo_id}/parts", response_model=WorkOrderPartResponse, status_code=201)
def consume_wo_part(
    wo_id: str,
    req: ConsumePartRequest,
    request: Request,
    db: Session = Depends(get_db),
    current_user: UserResponse = Depends(require_roles("manager", "admin")),
    idempotency_key: Optional[str] = Header(None),
):
    # Consuming a part decrements stock — a retried offline request must not
    # double-decrement. If this key already ran, return that result unchanged.
    cached = idempotency_service.get_cached(db, idempotency_key, current_user.id, request)
    if cached is not None:
        return cached
    wo = _get_wo_or_404(db, wo_id, current_user)
    part = db.query(Part).filter(Part.id == req.partId, Part.deleted_at.is_(None)).first()
    if not part:
        raise HTTPException(status_code=404, detail="Part not found")
    try:
        wp = parts_svc.consume_part(db, wo, part, req.quantity)
    except parts_svc.InsufficientStockError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    resp = parts_svc.wo_part_to_response(wp)
    idempotency_service.store(db, idempotency_key, current_user.id, request, 201, resp)
    return resp


# ---------------------------------------------------------------------------
# Vendor / external maintenance (Tier 3)
# PATCH /{wo_id}/assign-vendor — attach a vendor + SLA deadline
# ---------------------------------------------------------------------------

@router.patch("/{wo_id}/assign-vendor", response_model=WorkOrderDetailResponse)
def assign_vendor(
    wo_id: str,
    req: AssignVendorRequest,
    db: Session = Depends(get_db),
    current_user: UserResponse = Depends(require_roles("manager", "admin")),
):
    from datetime import date as _date
    wo = _get_wo_or_404(db, wo_id, current_user)
    vendor = db.query(Vendor).filter(Vendor.id == req.vendorId).first()
    if not vendor:
        raise HTTPException(status_code=404, detail="Vendor not found")
    sla_due = None
    if req.slaDue:
        try:
            sla_due = _date.fromisoformat(req.slaDue)
        except ValueError:
            raise HTTPException(status_code=422, detail="slaDue must be an ISO date (YYYY-MM-DD)")
    vendor_svc.assign_vendor(db, wo, vendor, sla_due)
    db.refresh(wo)
    return svc.wo_to_detail_response(wo)
