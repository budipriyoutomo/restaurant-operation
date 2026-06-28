import uuid
from sqlalchemy import Column, String, TIMESTAMP
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.sql import func

from app.database import Base


class AuditLog(Base):
    __tablename__ = "audit_logs"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    table_name = Column(String(50), nullable=False)
    record_id = Column(String(50), nullable=False)
    action = Column(String(30), nullable=False)   # create | update | delete | status_change
    old_value = Column(JSONB, nullable=True)
    new_value = Column(JSONB, nullable=True)
    performed_by = Column(String(200), nullable=False, default="system")
    created_at = Column(TIMESTAMP(timezone=True), server_default=func.now(), nullable=False)
