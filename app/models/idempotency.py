import uuid

from sqlalchemy import Column, ForeignKey, Integer, String, Text, TIMESTAMP
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.sql import func

from app.database import Base


class IdempotencyKey(Base):
    """A stored response, keyed by (client-supplied key, user), so a retried
    mutation returns the original result instead of running twice (Tier 5.3)."""

    __tablename__ = "idempotency_keys"

    key = Column(String(80), primary_key=True)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    method = Column(String(10), nullable=False)
    path = Column(String(300), nullable=False)
    status_code = Column(Integer, nullable=False)
    response_body = Column(Text, nullable=False)   # JSON of the response model
    created_at = Column(TIMESTAMP(timezone=True), server_default=func.now(), nullable=False)
