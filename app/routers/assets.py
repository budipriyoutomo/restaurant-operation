from datetime import datetime
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, text
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.asset import Asset, AssetNumberSequence, WorkOrder
from app.models.meter_reading import MeterReading
from app.schemas.asset import (
    AssetHistoryResponse,
    AssetResponse,
    AssetSummaryResponse,
    CreateAssetRequest,
    QRResolveResponse,
    UpdateAssetRequest,
)
from app.schemas.pm_schedule import CreateMeterReadingRequest, MeterReadingResponse
from app.services import work_order_service as wo_svc
from app.services.audit_service import write_audit
from app.services.outlet_scope_service import assert_can_access, assert_can_write_outlet, resolve_outlet_id, scoped_query
from app.services.auth_service import UserResponse, get_current_user, require_roles
from app.services.work_order_service import compute_downtime_hours

router = APIRouter(prefix="/api/assets", tags=["cmms"])


def _next_asset_number(db: Session) -> str:
    year = datetime.now().year
    result = db.execute(
        text("""
            INSERT INTO asset_number_sequences (year, last_seq)
            VALUES (:year, 1)
            ON CONFLICT (year) DO UPDATE
              SET last_seq = asset_number_sequences.last_seq + 1
            RETURNING last_seq
        """),
        {"year": year},
    )
    seq = result.scalar_one()
    return f"AST-{year}-{seq:05d}"


def _parse_date(date_str: Optional[str]):
    if not date_str:
        return None
    try:
        from datetime import date
        return date.fromisoformat(date_str)
    except ValueError:
        return None


def _to_response(asset: Asset) -> AssetResponse:
    return AssetResponse(
        id=str(asset.id),
        number=asset.number,
        name=asset.name,
        category=asset.category,
        outlet=asset.outlet,
        status=asset.status.value if hasattr(asset.status, "value") else str(asset.status),
        serialNumber=asset.serial_number,
        brand=asset.brand,
        model=asset.model,
        installDate=asset.install_date.isoformat() if asset.install_date else None,
        lastPM=asset.last_pm.isoformat() if asset.last_pm else None,
        nextPM=asset.next_pm.isoformat() if asset.next_pm else None,
        purchaseCost=asset.purchase_cost,
        qrToken=asset.qr_token,
        createdAt=asset.created_at.isoformat() if asset.created_at else "",
    )


@router.get("", response_model=List[AssetResponse])
def list_assets(
    outlet: Optional[str] = Query(None),
    status: Optional[str] = Query(None),
    db: Session = Depends(get_db),
    current_user: UserResponse = Depends(get_current_user),
):
    query = scoped_query(db, Asset, current_user)
    if outlet:
        query = query.filter(Asset.outlet == outlet)
    if status:
        query = query.filter(Asset.status == status)
    return [_to_response(a) for a in query.order_by(Asset.name).all()]


@router.post("", response_model=AssetResponse, status_code=201)
def create_asset(req: CreateAssetRequest, db: Session = Depends(get_db), current_user: UserResponse = Depends(require_roles("manager", "admin"))):
    outlet_id = resolve_outlet_id(db, req.outlet)
    assert_can_write_outlet(db, outlet_id, current_user)
    number = _next_asset_number(db)
    asset = Asset(
        number=number,
        name=req.name,
        category=req.category,
        outlet=req.outlet,
        outlet_id=outlet_id,
        status=req.status,
        serial_number=req.serialNumber,
        brand=req.brand,
        model=req.model,
        install_date=_parse_date(req.installDate),
        last_pm=_parse_date(req.lastPM),
        next_pm=_parse_date(req.nextPM),
        purchase_cost=req.purchaseCost,
    )
    db.add(asset)
    db.flush()
    write_audit(db, table_name="assets", record_id=str(asset.id), action="create",
                new_value={"number": asset.number, "name": asset.name, "outlet": asset.outlet})
    db.commit()
    db.refresh(asset)
    return _to_response(asset)


@router.get("/by-qr/{token}", response_model=QRResolveResponse)
def resolve_qr(
    token: str,
    db: Session = Depends(get_db),
    current_user: UserResponse = Depends(get_current_user),
):
    """Resolve a scanned QR sticker to its asset + the WO to jump into (Tier 5.2).

    Outlet-scoped: a token for another outlet's asset resolves to 404, same as if
    it didn't exist — a scanned sticker never leaks cross-outlet.
    """
    asset = db.query(Asset).filter(Asset.qr_token == token).first()
    if not asset:
        raise HTTPException(status_code=404, detail="Unknown QR code")
    assert_can_access(db, asset, current_user)

    active_statuses = ("scheduled", "in-progress", "on-hold")
    open_wos = (
        db.query(WorkOrder)
        .filter(WorkOrder.asset_id == asset.id, WorkOrder.status.in_(active_statuses))
        .order_by(WorkOrder.created_at.desc())
        .all()
    )
    return QRResolveResponse(
        asset=_to_response(asset),
        activeWorkOrderId=str(open_wos[0].id) if open_wos else None,
        openWorkOrderCount=len(open_wos),
    )


@router.get("/{asset_id}", response_model=AssetResponse)
def get_asset(asset_id: str, db: Session = Depends(get_db), current_user: UserResponse = Depends(get_current_user)):
    asset = db.query(Asset).filter(Asset.id == asset_id).first()
    if not asset:
        raise HTTPException(status_code=404, detail="Asset not found")
    assert_can_access(db, asset, current_user)
    return _to_response(asset)


@router.patch("/{asset_id}", response_model=AssetResponse)
def update_asset(asset_id: str, req: UpdateAssetRequest, db: Session = Depends(get_db), current_user: UserResponse = Depends(require_roles("manager", "admin"))):
    asset = db.query(Asset).filter(Asset.id == asset_id).first()
    if not asset:
        raise HTTPException(status_code=404, detail="Asset not found")
    assert_can_access(db, asset, current_user)

    old = {"number": asset.number, "status": str(asset.status), "name": asset.name}

    if req.name is not None:
        asset.name = req.name
    if req.category is not None:
        asset.category = req.category
    if req.outlet is not None:
        asset.outlet = req.outlet
        # Keep the FK in step with the display name, else scoping goes stale.
        asset.outlet_id = resolve_outlet_id(db, req.outlet)
    if req.status is not None:
        asset.status = req.status
    if req.serialNumber is not None:
        asset.serial_number = req.serialNumber
    if req.brand is not None:
        asset.brand = req.brand
    if req.model is not None:
        asset.model = req.model
    if req.installDate is not None:
        asset.install_date = _parse_date(req.installDate)
    if req.lastPM is not None:
        asset.last_pm = _parse_date(req.lastPM)
    if req.nextPM is not None:
        asset.next_pm = _parse_date(req.nextPM)
    if req.purchaseCost is not None:
        asset.purchase_cost = req.purchaseCost

    write_audit(db, table_name="assets", record_id=str(asset.id), action="update",
                old_value=old,
                new_value={"number": asset.number, "status": str(asset.status), "name": asset.name})
    db.commit()
    db.refresh(asset)
    return _to_response(asset)


@router.delete("/{asset_id}", status_code=204)
def delete_asset(asset_id: str, db: Session = Depends(get_db), current_user: UserResponse = Depends(require_roles("manager", "admin"))):
    asset = db.query(Asset).filter(Asset.id == asset_id).first()
    if not asset:
        raise HTTPException(status_code=404, detail="Asset not found")
    assert_can_access(db, asset, current_user)
    write_audit(db, table_name="assets", record_id=str(asset.id), action="delete",
                old_value={"number": asset.number, "name": asset.name})
    db.delete(asset)
    db.commit()


# ---------------------------------------------------------------------------
# GET /{asset_id}/history — paginated work order list for an asset
# ---------------------------------------------------------------------------

@router.get("/{asset_id}/history", response_model=AssetHistoryResponse)
def get_asset_history(
    asset_id: str,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db),
    current_user: UserResponse = Depends(get_current_user),
):
    """Return paginated work orders for an asset, newest first."""
    asset = db.query(Asset).filter(Asset.id == asset_id).first()
    if not asset:
        raise HTTPException(status_code=404, detail="Asset not found")
    assert_can_access(db, asset, current_user)

    query = (
        db.query(WorkOrder)
        .filter(WorkOrder.asset_id == asset_id)
        .order_by(WorkOrder.created_at.desc())
    )
    total = query.count()
    wos = query.offset((page - 1) * page_size).limit(page_size).all()

    return AssetHistoryResponse(
        items=[wo_svc.wo_to_response(wo) for wo in wos],
        total=total,
        page=page,
        pageSize=page_size,
    )


# ---------------------------------------------------------------------------
# GET /{asset_id}/summary — aggregated cost, downtime, PM dates
# ---------------------------------------------------------------------------

@router.get("/{asset_id}/summary", response_model=AssetSummaryResponse)
def get_asset_summary(
    asset_id: str,
    db: Session = Depends(get_db),
    current_user: UserResponse = Depends(get_current_user),
):
    """Return aggregate maintenance stats for an asset."""
    asset = db.query(Asset).filter(Asset.id == asset_id).first()
    if not asset:
        raise HTTPException(status_code=404, detail="Asset not found")
    assert_can_access(db, asset, current_user)

    wos = db.query(WorkOrder).filter(WorkOrder.asset_id == asset_id).all()

    total_labor   = sum(int(wo.labor_cost  or 0) for wo in wos)
    total_parts   = sum(int(wo.parts_cost  or 0) for wo in wos)
    total_cost    = total_labor + total_parts
    total_downtime = sum(
        compute_downtime_hours(wo.downtime_start, wo.downtime_end)
        for wo in wos
    )

    # Count WOs created in the last 90 days
    from datetime import date, timedelta
    cutoff = datetime.now().date() - timedelta(days=90)
    recent_count = sum(
        1 for wo in wos
        if wo.created_at and wo.created_at.date() >= cutoff
    )

    return AssetSummaryResponse(
        totalWorkOrders=len(wos),
        totalDowntimeHours=round(total_downtime, 2),
        totalLaborCost=total_labor,
        totalPartsCost=total_parts,
        totalCost=total_cost,
        lastPM=asset.last_pm.isoformat() if asset.last_pm else None,
        nextPM=asset.next_pm.isoformat() if asset.next_pm else None,
        workOrdersLast90Days=recent_count,
    )


# ---------------------------------------------------------------------------
# Meter readings (meter-based PM, Tier 3)
# ---------------------------------------------------------------------------

def _reading_to_response(r: MeterReading) -> MeterReadingResponse:
    return MeterReadingResponse(
        id=str(r.id),
        assetId=str(r.asset_id),
        value=int(r.value),
        note=r.note,
        recordedBy=str(r.recorded_by) if r.recorded_by else None,
        recordedAt=r.recorded_at.isoformat() if r.recorded_at else "",
    )


@router.get("/{asset_id}/meter-readings", response_model=List[MeterReadingResponse])
def list_meter_readings(
    asset_id: str,
    limit: int = Query(50, ge=1, le=200),
    db: Session = Depends(get_db),
    current_user: UserResponse = Depends(get_current_user),
):
    asset = db.query(Asset).filter(Asset.id == asset_id).first()
    if not asset:
        raise HTTPException(status_code=404, detail="Asset not found")
    assert_can_access(db, asset, current_user)
    readings = (
        db.query(MeterReading)
        .filter(MeterReading.asset_id == asset_id)
        .order_by(MeterReading.recorded_at.desc())
        .limit(limit)
        .all()
    )
    return [_reading_to_response(r) for r in readings]


@router.post("/{asset_id}/meter-readings", response_model=MeterReadingResponse, status_code=201)
def create_meter_reading(
    asset_id: str,
    req: CreateMeterReadingRequest,
    db: Session = Depends(get_db),
    current_user: UserResponse = Depends(get_current_user),
):
    asset = db.query(Asset).filter(Asset.id == asset_id).first()
    if not asset:
        raise HTTPException(status_code=404, detail="Asset not found")
    assert_can_access(db, asset, current_user)
    import uuid as _uuid
    recorder = None
    try:
        recorder = _uuid.UUID(current_user.id)
    except (ValueError, TypeError):
        recorder = None
    reading = MeterReading(asset_id=asset_id, value=req.value, note=req.note, recorded_by=recorder)
    db.add(reading)
    db.commit()
    db.refresh(reading)
    return _reading_to_response(reading)
