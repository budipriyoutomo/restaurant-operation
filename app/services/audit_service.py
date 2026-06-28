"""Audit log helpers — write one row per action."""

from typing import Any, Optional
from sqlalchemy.orm import Session
from app.models.audit_log import AuditLog


def write_audit(
    db: Session,
    *,
    table_name: str,
    record_id: str,
    action: str,
    old_value: Optional[dict] = None,
    new_value: Optional[dict] = None,
    performed_by: str = "system",
) -> None:
    """Append an audit entry. Call before commit so it shares the transaction."""
    entry = AuditLog(
        table_name=table_name,
        record_id=str(record_id),
        action=action,
        old_value=old_value,
        new_value=new_value,
        performed_by=performed_by,
    )
    db.add(entry)
