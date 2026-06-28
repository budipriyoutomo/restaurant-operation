import uuid
from sqlalchemy import Column, String, Text, Numeric, Integer, Date, TIMESTAMP
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.sql import func

from app.database import Base


class TrainingProgram(Base):
    __tablename__ = "training_programs"

    id               = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    title            = Column(String(300), nullable=False)
    description      = Column(Text, nullable=True)
    target_role      = Column(String(100), nullable=False, default="staff")
    outlet           = Column(String(200), nullable=True)
    trainer          = Column(String(200), nullable=True)
    scheduled_date   = Column(Date, nullable=True)
    duration_hours   = Column(Numeric(5, 1), nullable=True)
    status           = Column(String(20), nullable=False, default="scheduled")
    max_participants = Column(Integer, nullable=True)
    created_at       = Column(TIMESTAMP(timezone=True), server_default=func.now(), nullable=False)
    updated_at       = Column(TIMESTAMP(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)
