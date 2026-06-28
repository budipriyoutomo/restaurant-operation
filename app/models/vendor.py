import uuid
from sqlalchemy import Column, String, Boolean, Text, TIMESTAMP
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.sql import func

from app.database import Base


class Vendor(Base):
    __tablename__ = "vendors"

    id            = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name          = Column(String(300), nullable=False)
    category      = Column(String(100), nullable=False, default="General")
    contact_name  = Column(String(200), nullable=True)
    contact_phone = Column(String(50), nullable=True)
    contact_email = Column(String(200), nullable=True)
    address       = Column(Text, nullable=True)
    outlet        = Column(String(200), nullable=True)
    is_active     = Column(Boolean, nullable=False, default=True)
    notes         = Column(Text, nullable=True)
    created_at    = Column(TIMESTAMP(timezone=True), server_default=func.now(), nullable=False)
    updated_at    = Column(TIMESTAMP(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)
