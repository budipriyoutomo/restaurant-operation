"""Training participant (Todo-Pilot §9, migration 038)."""

import uuid

from sqlalchemy import CheckConstraint, Column, ForeignKey, Integer, String, Text, TIMESTAMP, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.sql import func

from app.core.tenancy import TenantScoped
from app.database import Base


class TrainingEnrollment(TenantScoped, Base):
    __tablename__ = "training_enrollments"
    __table_args__ = (
        UniqueConstraint("program_id", "user_id", name="uq_training_enrollment"),
        CheckConstraint("status IN ('registered', 'attended', 'no-show')", name="training_enrollments_status_check"),
        CheckConstraint("score BETWEEN 0 AND 100", name="training_enrollments_score_check"),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    program_id = Column(UUID(as_uuid=True), ForeignKey("training_programs.id", ondelete="CASCADE"), nullable=False, index=True)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    # Snapshots at enrollment — the recap must not shift when people move.
    user_name = Column(String(200), nullable=False, default="", server_default="")
    role_name = Column(String(100), nullable=False, default="", server_default="")
    outlet = Column(String(200), nullable=False, default="", server_default="")
    status = Column(String(20), nullable=False, default="registered", server_default="registered")
    score = Column(Integer, nullable=True)
    notes = Column(Text, nullable=False, default="", server_default="")
    enrolled_at = Column(TIMESTAMP(timezone=True), server_default=func.now(), nullable=False)
    marked_at = Column(TIMESTAMP(timezone=True), nullable=True)
