from typing import List, Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.audit_log import AuditLog
from app.schemas.audit_log import AuditLogResponse

router = APIRouter(prefix="/api/audit-logs", tags=["audit"])


def _to_response(entry: AuditLog) -> AuditLogResponse:
    return AuditLogResponse(
        id=str(entry.id),
        table_name=entry.table_name,
        record_id=entry.record_id,
        action=entry.action,
        old_value=entry.old_value,
        new_value=entry.new_value,
        performed_by=entry.performed_by,
        created_at=entry.created_at.isoformat() if entry.created_at else "",
    )


@router.get("", response_model=List[AuditLogResponse])
def list_audit_logs(
    table_name: Optional[str] = Query(None, description="Filter by table name (e.g. outlets, issues)"),
    record_id: Optional[str] = Query(None, description="Filter by record UUID"),
    limit: int = Query(100, ge=1, le=500),
    db: Session = Depends(get_db),
):
    """Return audit log entries, newest first. Filter by table_name and/or record_id."""
    q = db.query(AuditLog)
    if table_name:
        q = q.filter(AuditLog.table_name == table_name)
    if record_id:
        q = q.filter(AuditLog.record_id == record_id)
    entries = q.order_by(AuditLog.created_at.desc()).limit(limit).all()
    return [_to_response(e) for e in entries]
