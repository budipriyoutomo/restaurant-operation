import uuid

from sqlalchemy import BigInteger, Column, ForeignKey, Integer, String, Text, TIMESTAMP
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from app.database import Base


class PurchaseRequest(Base):
    """A request to buy parts. Raised manually or automatically when stock hits
    the reorder level; approved through the shared approval engine."""

    __tablename__ = "purchase_requests"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    number = Column(String(30), nullable=False, unique=True)       # PR-2026-00001
    status = Column(String(20), nullable=False, default="pending_approval")
    outlet = Column(String(200), nullable=True)
    outlet_id = Column(UUID(as_uuid=True), ForeignKey("outlets.id", ondelete="RESTRICT"), nullable=True)
    source = Column(String(20), nullable=False, default="manual")  # manual | auto_reorder
    requested_by = Column(String(200), nullable=True)
    notes = Column(Text, nullable=True)
    total_est = Column(BigInteger, nullable=False, default=0)          # IDR
    created_at = Column(TIMESTAMP(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(TIMESTAMP(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)

    items = relationship("PurchaseRequestItem", back_populates="request",
                         cascade="all, delete-orphan", lazy="selectin")
    # The polymorphic approval (type=procurement) attached to this PR.
    approval = relationship("ApprovalRequest", back_populates="purchase_request",
                            uselist=False, lazy="selectin",
                            foreign_keys="ApprovalRequest.purchase_request_id")


class PurchaseRequestItem(Base):
    __tablename__ = "purchase_request_items"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    purchase_request_id = Column(UUID(as_uuid=True), ForeignKey("purchase_requests.id", ondelete="CASCADE"), nullable=False)
    part_id = Column(UUID(as_uuid=True), ForeignKey("parts.id", ondelete="SET NULL"), nullable=True)
    part_name = Column(String(300), nullable=False)
    quantity = Column(Integer, nullable=False)
    est_unit_cost = Column(BigInteger, nullable=False, default=0)
    line_total = Column(BigInteger, nullable=False, default=0)

    request = relationship("PurchaseRequest", back_populates="items")


class PurchaseOrder(Base):
    __tablename__ = "purchase_orders"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    number = Column(String(30), nullable=False, unique=True)        # PO-2026-00001
    purchase_request_id = Column(UUID(as_uuid=True), ForeignKey("purchase_requests.id", ondelete="SET NULL"), nullable=True)
    vendor_id = Column(UUID(as_uuid=True), ForeignKey("vendors.id", ondelete="RESTRICT"), nullable=True)
    vendor_name = Column(String(200), nullable=True)
    status = Column(String(20), nullable=False, default="sent")     # sent | partially_received | received | cancelled
    outlet = Column(String(200), nullable=True)
    outlet_id = Column(UUID(as_uuid=True), ForeignKey("outlets.id", ondelete="RESTRICT"), nullable=True)
    total = Column(BigInteger, nullable=False, default=0)
    created_by = Column(String(200), nullable=True)
    created_at = Column(TIMESTAMP(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(TIMESTAMP(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)

    items = relationship("PurchaseOrderItem", back_populates="order",
                         cascade="all, delete-orphan", lazy="selectin")


class PurchaseOrderItem(Base):
    __tablename__ = "purchase_order_items"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    purchase_order_id = Column(UUID(as_uuid=True), ForeignKey("purchase_orders.id", ondelete="CASCADE"), nullable=False)
    part_id = Column(UUID(as_uuid=True), ForeignKey("parts.id", ondelete="SET NULL"), nullable=True)
    part_name = Column(String(300), nullable=False)
    quantity_ordered = Column(Integer, nullable=False)
    quantity_received = Column(Integer, nullable=False, default=0)
    unit_cost = Column(BigInteger, nullable=False, default=0)
    line_total = Column(BigInteger, nullable=False, default=0)

    order = relationship("PurchaseOrder", back_populates="items")


class GoodsReceipt(Base):
    __tablename__ = "goods_receipts"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    number = Column(String(30), nullable=False, unique=True)        # GRN-2026-00001
    purchase_order_id = Column(UUID(as_uuid=True), ForeignKey("purchase_orders.id", ondelete="CASCADE"), nullable=False)
    received_by = Column(String(200), nullable=True)
    notes = Column(Text, nullable=True)
    received_at = Column(TIMESTAMP(timezone=True), server_default=func.now(), nullable=False)
    created_at = Column(TIMESTAMP(timezone=True), server_default=func.now(), nullable=False)

    items = relationship("GoodsReceiptItem", back_populates="receipt",
                         cascade="all, delete-orphan", lazy="selectin")


class GoodsReceiptItem(Base):
    __tablename__ = "goods_receipt_items"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    goods_receipt_id = Column(UUID(as_uuid=True), ForeignKey("goods_receipts.id", ondelete="CASCADE"), nullable=False)
    purchase_order_item_id = Column(UUID(as_uuid=True), ForeignKey("purchase_order_items.id", ondelete="CASCADE"), nullable=False)
    part_id = Column(UUID(as_uuid=True), ForeignKey("parts.id", ondelete="SET NULL"), nullable=True)
    quantity_received = Column(Integer, nullable=False)

    receipt = relationship("GoodsReceipt", back_populates="items")


class ProcurementNumberSequence(Base):
    __tablename__ = "procurement_number_sequences"

    prefix = Column(String(8), primary_key=True)
    year = Column(Integer, primary_key=True)
    last_seq = Column(Integer, nullable=False, default=0)
