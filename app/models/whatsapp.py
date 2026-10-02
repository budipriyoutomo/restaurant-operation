import uuid

from sqlalchemy import CheckConstraint, Column, ForeignKey, Index, Integer, String, Text, TIMESTAMP
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.sql import func

from app.database import Base


class WhatsAppMessage(Base):
    """One outgoing WhatsApp message (Todo-Pilot §4, migration 035).

    status: pending → sent | failed (after WHATSAPP_MAX_ATTEMPTS) — or skipped
    when the recipient's hourly limit was already reached (kept for visibility).
    """

    __tablename__ = "whatsapp_outbox"
    __table_args__ = (
        CheckConstraint("status IN ('pending', 'sent', 'failed', 'skipped')", name="ck_whatsapp_outbox_status"),
        Index("ix_whatsapp_outbox_due", "status", "next_attempt_at"),
        Index("ix_whatsapp_outbox_user_created", "user_id", "created_at"),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    phone = Column(String(20), nullable=False)
    event = Column(String(50), nullable=False)
    entity_type = Column(String(50), nullable=True)
    entity_id = Column(UUID(as_uuid=True), nullable=True)
    body = Column(Text, nullable=False)
    status = Column(String(20), nullable=False, default="pending", server_default="pending")
    attempts = Column(Integer, nullable=False, default=0, server_default="0")
    last_error = Column(Text, nullable=True)
    next_attempt_at = Column(TIMESTAMP(timezone=True), nullable=False, server_default=func.now())
    sent_at = Column(TIMESTAMP(timezone=True), nullable=True)
    created_at = Column(TIMESTAMP(timezone=True), server_default=func.now(), nullable=False)
