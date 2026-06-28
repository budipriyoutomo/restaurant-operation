import uuid
from sqlalchemy import Column, String, Text, TIMESTAMP, ForeignKey
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.sql import func

from app.database import Base


class Notification(Base):
    __tablename__ = "notifications"

    id          = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id     = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    title       = Column(Text, nullable=False)
    message     = Column(Text, nullable=False)
    type        = Column(String(20), nullable=False, default="info")  # info | warning | critical | success
    entity_type = Column(String(50), nullable=True)
    entity_id   = Column(UUID(as_uuid=True), nullable=True)
    read_at     = Column(TIMESTAMP(timezone=True), nullable=True)
    created_at  = Column(TIMESTAMP(timezone=True), server_default=func.now(), nullable=False)
