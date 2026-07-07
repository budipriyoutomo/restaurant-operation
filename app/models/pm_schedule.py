import uuid

from sqlalchemy import (
    Boolean,
    Column,
    Date,
    ForeignKey,
    Integer,
    String,
    TIMESTAMP,
    Enum as SAEnum,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from app.database import Base
from app.models.enums import ApproverRoleEnum, PMIntervalTypeEnum, PMTriggerTypeEnum


def _sa_enum(py_enum, pg_name):
    return SAEnum(
        py_enum,
        values_callable=lambda x: [e.value for e in x],
        name=pg_name,
        create_type=False,
    )


class PMSchedule(Base):
    """A recurring preventive-maintenance definition for a single asset.

    The scheduler (`generate_due_preventive_work_orders`) turns due schedules
    into preventive Work Orders and advances `next_due_date`.
    """

    __tablename__ = "pm_schedules"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    asset_id = Column(
        UUID(as_uuid=True),
        ForeignKey("assets.id", ondelete="CASCADE"),
        nullable=False,
    )
    name = Column(String(300), nullable=False)
    interval_type = Column(
        _sa_enum(PMIntervalTypeEnum, "pm_interval_type"),
        nullable=False,
        default=PMIntervalTypeEnum.days,
    )
    interval_value = Column(Integer, nullable=False, default=30)
    checklist = Column(JSONB, nullable=False, default=list)          # list[str]
    assignee_role = Column(_sa_enum(ApproverRoleEnum, "approver_role"), nullable=True)
    assignee_user_id = Column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    assignee_name = Column(String(200), nullable=True)              # denormalized for WO display
    lead_time_days = Column(Integer, nullable=False, default=0)
    # Calendar trigger fields (nullable — meter schedules leave next_due_date empty)
    next_due_date = Column(Date, nullable=True)
    # Meter trigger fields (migration 019)
    trigger_type = Column(
        _sa_enum(PMTriggerTypeEnum, "pm_trigger_type"),
        nullable=False,
        default=PMTriggerTypeEnum.calendar,
    )
    meter_interval = Column(Integer, nullable=True)     # generate every N meter units
    last_meter_value = Column(Integer, nullable=True)   # meter value at last generation
    last_generated_at = Column(TIMESTAMP(timezone=True), nullable=True)
    is_active = Column(Boolean, nullable=False, default=True)
    outlet = Column(String(200), nullable=False)                   # denormalized
    created_at = Column(TIMESTAMP(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(TIMESTAMP(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)
    deleted_at = Column(TIMESTAMP(timezone=True), nullable=True)

    asset = relationship("Asset")
