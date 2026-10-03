import uuid
from sqlalchemy import ForeignKey, Column, String, Text, Numeric, Integer, Date, TIMESTAMP, Enum as SAEnum
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from app.core.tenancy import TenantScoped
from app.database import Base


class TrainingProgram(TenantScoped, Base):
    __tablename__ = "training_programs"

    id               = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    title            = Column(String(300), nullable=False)
    description      = Column(Text, nullable=True)
    target_role      = Column(String(100), nullable=False, default="staff")
    outlet           = Column(String(200), nullable=True)
    # Real FK alongside the denormalised name (migration 024). Nullable:
    # NULL means "not tied to one outlet" (shared / All Outlets).
    outlet_id = Column(UUID(as_uuid=True), ForeignKey("outlets.id", ondelete="RESTRICT"), nullable=True)
    trainer          = Column(String(200), nullable=True)
    scheduled_date   = Column(Date, nullable=True)
    duration_hours   = Column(Numeric(5, 1), nullable=True)
    # Native Postgres enum in the migrations — must not be String (psycopg would
    # send ::VARCHAR and every INSERT would fail). See test_unit_model_enums.
    status           = Column(SAEnum("scheduled", "ongoing", "completed", "cancelled",
                                     name="training_program_status", create_type=False),
                              nullable=False, default="scheduled")
    max_participants = Column(Integer, nullable=True)
    created_at       = Column(TIMESTAMP(timezone=True), server_default=func.now(), nullable=False)
    updated_at       = Column(TIMESTAMP(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)

    enrollments = relationship(
        "TrainingEnrollment", cascade="all, delete-orphan", passive_deletes=True,
        order_by="TrainingEnrollment.user_name", lazy="selectin",
    )
