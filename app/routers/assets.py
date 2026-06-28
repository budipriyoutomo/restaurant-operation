from datetime import datetime
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.asset import Asset, AssetNumberSequence
from app.schemas.asset import AssetResponse, CreateAssetRequest, UpdateAssetRequest
from app.services.audit_service import write_audit
from app.services.auth_service import UserResponse, get_current_user, require_roles

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
        createdAt=asset.created_at.isoformat() if asset.created_at else "",
    )


@router.get("", response_model=List[AssetResponse])
def list_assets(
    outlet: Optional[str] = Query(None),
    status: Optional[str] = Query(None),
    db: Session = Depends(get_db),
    _: UserResponse = Depends(get_current_user),
):
    query = db.query(Asset)
    if outlet:
        query = query.filter(Asset.outlet == outlet)
    if status:
        query = query.filter(Asset.status == status)
    return [_to_response(a) for a in query.order_by(Asset.name).all()]


@router.post("", response_model=AssetResponse, status_code=201)
def create_asset(req: CreateAssetRequest, db: Session = Depends(get_db), _: UserResponse = Depends(require_roles("manager", "admin"))):
    number = _next_asset_number(db)
    asset = Asset(
        number=number,
        name=req.name,
        category=req.category,
        outlet=req.outlet,
        status=req.status,
        serial_number=req.serialNumber,
        brand=req.brand,
        model=req.model,
        install_date=_parse_date(req.installDate),
        last_pm=_parse_date(req.lastPM),
        next_pm=_parse_date(req.nextPM),
    )
    db.add(asset)
    db.flush()
    write_audit(db, table_name="assets", record_id=str(asset.id), action="create",
                new_value={"number": asset.number, "name": asset.name, "outlet": asset.outlet})
    db.commit()
    db.refresh(asset)
    return _to_response(asset)


@router.get("/{asset_id}", response_model=AssetResponse)
def get_asset(asset_id: str, db: Session = Depends(get_db), _: UserResponse = Depends(get_current_user)):
    asset = db.query(Asset).filter(Asset.id == asset_id).first()
    if not asset:
        raise HTTPException(status_code=404, detail="Asset not found")
    return _to_response(asset)


@router.patch("/{asset_id}", response_model=AssetResponse)
def update_asset(asset_id: str, req: UpdateAssetRequest, db: Session = Depends(get_db), _: UserResponse = Depends(require_roles("manager", "admin"))):
    asset = db.query(Asset).filter(Asset.id == asset_id).first()
    if not asset:
        raise HTTPException(status_code=404, detail="Asset not found")

    old = {"number": asset.number, "status": str(asset.status), "name": asset.name}

    if req.name is not None:
        asset.name = req.name
    if req.category is not None:
        asset.category = req.category
    if req.outlet is not None:
        asset.outlet = req.outlet
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

    write_audit(db, table_name="assets", record_id=str(asset.id), action="update",
                old_value=old,
                new_value={"number": asset.number, "status": str(asset.status), "name": asset.name})
    db.commit()
    db.refresh(asset)
    return _to_response(asset)


@router.delete("/{asset_id}", status_code=204)
def delete_asset(asset_id: str, db: Session = Depends(get_db), _: UserResponse = Depends(require_roles("manager", "admin"))):
    asset = db.query(Asset).filter(Asset.id == asset_id).first()
    if not asset:
        raise HTTPException(status_code=404, detail="Asset not found")
    write_audit(db, table_name="assets", record_id=str(asset.id), action="delete",
                old_value={"number": asset.number, "name": asset.name})
    db.delete(asset)
    db.commit()
