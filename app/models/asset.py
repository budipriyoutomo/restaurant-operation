import uuid
from sqlalchemy import Column, String, Date, Text, Integer, Boolean, Numeric, Enum as SAEnum, TIMESTAMP, ForeignKey
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from app.database import Base
from app.models.enums import AssetStatusEnum, PriorityEnum, WorkOrderTypeEnum, WorkOrderStatusEnum


def _sa_enum(py_enum, pg_name):
    return SAEnum(
        py_enum,
        values_callable=lambda x: [e.value for e in x],
        name=pg_name,
        create_type=False,
    )


class Asset(Base):
    __tablename__ = "assets"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    number = Column(String(30), nullable=False, unique=True)      # AST-2026-00001
    name = Column(String(300), nullable=False)
    category = Column(String(100), nullable=False)                # free-text, e.g. "AC Unit"
    outlet = Column(String(200), nullable=False)                  # denormalized
    status = Column(_sa_enum(AssetStatusEnum, "asset_status"), nullable=False, default=AssetStatusEnum.operational)
    serial_number = Column(String(100))
    brand = Column(String(100))
    model = Column(String(100))
    install_date = Column(Date)
    last_pm = Column(Date)
    next_pm = Column(Date)
    created_at = Column(TIMESTAMP(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(TIMESTAMP(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)

    work_orders = relationship(
        "WorkOrder",
        back_populates="asset",
        cascade="all, delete-orphan",
        lazy="selectin",
    )


class AssetNumberSequence(Base):
    __tablename__ = "asset_number_sequences"

    year = Column(Integer, primary_key=True)
    last_seq = Column(Integer, nullable=False, default=0)


class WorkOrder(Base):
    __tablename__ = "work_orders"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    number = Column(String(30), nullable=False, unique=True)      # WO-2026-00001
    type = Column(_sa_enum(WorkOrderTypeEnum, "work_order_type"), nullable=False, default=WorkOrderTypeEnum.corrective)
    asset_id = Column(UUID(as_uuid=True), ForeignKey("assets.id", ondelete="SET NULL"), nullable=True)
    asset_name = Column(String(300), nullable=False)              # denormalized for display
    outlet = Column(String(200), nullable=False)                  # denormalized
    issue_id = Column(UUID(as_uuid=True), ForeignKey("issues.id", ondelete="SET NULL"), nullable=True)
    issue_number = Column(String(30))                             # denormalized, nullable
    title = Column(String(500), nullable=False)
    description = Column(Text, default="")
    priority = Column(_sa_enum(PriorityEnum, "priority"), nullable=False, default=PriorityEnum.medium)
    status = Column(_sa_enum(WorkOrderStatusEnum, "work_order_status"), nullable=False, default=WorkOrderStatusEnum.scheduled)
    assignee = Column(String(200), default="Unassigned")
    scheduled_date = Column(Date)
    completed_date = Column(Date)
    created_at = Column(TIMESTAMP(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(TIMESTAMP(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)

    # New columns — migration 012
    downtime_start    = Column(TIMESTAMP(timezone=True), nullable=True)
    downtime_end      = Column(TIMESTAMP(timezone=True), nullable=True)
    labor_hours       = Column(Numeric(8, 2), nullable=True)
    labor_cost        = Column(Numeric(14, 2), nullable=False, default=0)
    parts_cost        = Column(Numeric(14, 2), nullable=False, default=0)
    estimated_cost    = Column(Numeric(14, 2), nullable=True)
    requires_approval = Column(Boolean, nullable=False, default=False)
    approval_id       = Column(
        UUID(as_uuid=True),
        ForeignKey("approval_requests.id", ondelete="SET NULL"),
        nullable=True,
    )

    asset    = relationship("Asset", back_populates="work_orders")
    issue    = relationship("Issue", foreign_keys=[issue_id])
    approval = relationship("ApprovalRequest", foreign_keys=[approval_id])

    checklist_items = relationship(
        "WorkOrderChecklistItem",
        back_populates="work_order",
        cascade="all, delete-orphan",
        order_by="WorkOrderChecklistItem.order_index",
        lazy="selectin",
    )
    attachments = relationship(
        "WorkOrderAttachment",
        back_populates="work_order",
        cascade="all, delete-orphan",
        lazy="selectin",
    )


class WorkOrderChecklistItem(Base):
    __tablename__ = "work_order_checklist_items"

    id            = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    work_order_id = Column(UUID(as_uuid=True), ForeignKey("work_orders.id", ondelete="CASCADE"), nullable=False)
    title         = Column(String(500), nullable=False)
    is_done       = Column(Boolean, nullable=False, default=False)
    done_by       = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    done_at       = Column(TIMESTAMP(timezone=True), nullable=True)
    order_index   = Column(Integer, nullable=False, default=0)

    work_order = relationship("WorkOrder", back_populates="checklist_items")


class WorkOrderAttachment(Base):
    __tablename__ = "work_order_attachments"

    id            = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    work_order_id = Column(UUID(as_uuid=True), ForeignKey("work_orders.id", ondelete="CASCADE"), nullable=False)
    file_url      = Column(Text, nullable=False)
    caption       = Column(String(500), nullable=True)
    uploaded_by   = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)
    created_at    = Column(TIMESTAMP(timezone=True), server_default=func.now(), nullable=False)

    work_order = relationship("WorkOrder", back_populates="attachments")


class WorkOrderNumberSequence(Base):
    __tablename__ = "work_order_number_sequences"

    year = Column(Integer, primary_key=True)
    last_seq = Column(Integer, nullable=False, default=0)
