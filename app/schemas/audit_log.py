from typing import Optional
from pydantic import BaseModel


class AuditLogResponse(BaseModel):
    id: str
    table_name: str
    record_id: str
    action: str
    old_value: Optional[dict] = None
    new_value: Optional[dict] = None
    performed_by: str
    created_at: str
