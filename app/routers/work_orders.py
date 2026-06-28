from datetime import datetime
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.asset import Asset, WorkOrder, WorkOrderNumberSequence
from app.schemas.asset import CreateWorkOrderRequest, UpdateWorkOrderRequest, WorkOrderResponse
from app.services.audit_service import write_audit

router = APIRouter(prefix="/api/work-orders", tags=["cmms"])


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


def _to_response(wo: WorkOrder) -> WorkOrderResponse:
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
    )


@router.get("", response_model=List[WorkOrderResponse])
def list_work_orders(
    asset_id: Optional[str] = Query(None),
    issue_id: Optional[str] = Query(None),
    status: Optional[str] = Query(None),
    outlet: Optional[str] = Query(None),
    db: Session = Depends(get_db),
):
    query = db.query(WorkOrder)
    if asset_id:
        query = query.filter(WorkOrder.asset_id == asset_id)
    if issue_id:
        query = query.filter(WorkOrder.issue_id == issue_id)
    if status:
        query = query.filter(WorkOrder.status == status)
    if outlet:
        query = query.filter(WorkOrder.outlet == outlet)
    return [_to_response(wo) for wo in query.order_by(WorkOrder.created_at.desc()).all()]


@router.post("", response_model=WorkOrderResponse, status_code=201)
def create_work_order(req: CreateWorkOrderRequest, db: Session = Depends(get_db)):
    asset = db.query(Asset).filter(Asset.id == req.assetId).first()
    if not asset:
        raise HTTPException(status_code=404, detail="Asset not found")

    number = _next_wo_number(db)
    wo = WorkOrder(
        number=number,
        type=req.type,
        asset_id=asset.id,
        asset_name=asset.name,
        outlet=asset.outlet,
        issue_id=req.issueId or None,
        issue_number=req.issueNumber,
        title=req.title,
        description=req.description,
        priority=req.priority,
        assignee=req.assignee,
        scheduled_date=_parse_date(req.scheduledDate),
    )
    db.add(wo)
    db.flush()
    write_audit(db, table_name="work_orders", record_id=str(wo.id), action="create",
                new_value={"number": wo.number, "asset": wo.asset_name, "outlet": wo.outlet})
    db.commit()
    db.refresh(wo)
    return _to_response(wo)


@router.get("/{wo_id}", response_model=WorkOrderResponse)
def get_work_order(wo_id: str, db: Session = Depends(get_db)):
    wo = db.query(WorkOrder).filter(WorkOrder.id == wo_id).first()
    if not wo:
        raise HTTPException(status_code=404, detail="Work order not found")
    return _to_response(wo)


@router.patch("/{wo_id}", response_model=WorkOrderResponse)
def update_work_order(wo_id: str, req: UpdateWorkOrderRequest, db: Session = Depends(get_db)):
    wo = db.query(WorkOrder).filter(WorkOrder.id == wo_id).first()
    if not wo:
        raise HTTPException(status_code=404, detail="Work order not found")

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
    return _to_response(wo)


@router.delete("/{wo_id}", status_code=204)
def delete_work_order(wo_id: str, db: Session = Depends(get_db)):
    wo = db.query(WorkOrder).filter(WorkOrder.id == wo_id).first()
    if not wo:
        raise HTTPException(status_code=404, detail="Work order not found")
    write_audit(db, table_name="work_orders", record_id=str(wo.id), action="delete",
                old_value={"number": wo.number, "asset": wo.asset_name})
    db.delete(wo)
    db.commit()
