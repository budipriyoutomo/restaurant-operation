import uuid

from sqlalchemy import Boolean, Column, Integer, String, TIMESTAMP, ForeignKey
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from app.database import Base


class Part(Base):
    """Inventory master for a spare part."""

    __tablename__ = "parts"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    sku = Column(String(60), nullable=False, unique=True)
    name = Column(String(300), nullable=False)
    category = Column(String(100), nullable=False, default="General")
    unit = Column(String(20), nullable=False, default="pcs")
    unit_cost = Column(Integer, nullable=False, default=0)      # IDR
    stock_qty = Column(Integer, nullable=False, default=0)
    reorder_level = Column(Integer, nullable=False, default=0)
    outlet = Column(String(200), nullable=True)                # null = shared
    is_active = Column(Boolean, nullable=False, default=True)
    created_at = Column(TIMESTAMP(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(TIMESTAMP(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)
    deleted_at = Column(TIMESTAMP(timezone=True), nullable=True)


class WorkOrderPart(Base):
    """A quantity of a part consumed by a work order (decrements stock)."""

    __tablename__ = "work_order_parts"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    work_order_id = Column(UUID(as_uuid=True), ForeignKey("work_orders.id", ondelete="CASCADE"), nullable=False)
    part_id = Column(UUID(as_uuid=True), ForeignKey("parts.id", ondelete="SET NULL"), nullable=True)
    part_name = Column(String(300), nullable=False)            # denormalized snapshot
    quantity = Column(Integer, nullable=False)
    unit_cost = Column(Integer, nullable=False, default=0)     # IDR snapshot
    line_cost = Column(Integer, nullable=False, default=0)     # quantity * unit_cost
    created_at = Column(TIMESTAMP(timezone=True), server_default=func.now(), nullable=False)

    work_order = relationship("WorkOrder", back_populates="parts_used")
    part = relationship("Part")
