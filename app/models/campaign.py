import uuid
from sqlalchemy import Column, String, Text, Date, TIMESTAMP
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.sql import func

from app.database import Base


class Campaign(Base):
    __tablename__ = "campaigns"

    id          = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    title       = Column(String(300), nullable=False)
    type        = Column(String(50), nullable=False, default="other")
    description = Column(Text, nullable=True)
    outlet      = Column(String(200), nullable=True)
    budget      = Column(String(100), nullable=True)
    start_date  = Column(Date, nullable=True)
    end_date    = Column(Date, nullable=True)
    status      = Column(String(20), nullable=False, default="draft")
    pic         = Column(String(200), nullable=True)
    created_at  = Column(TIMESTAMP(timezone=True), server_default=func.now(), nullable=False)
    updated_at  = Column(TIMESTAMP(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)
