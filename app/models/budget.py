import uuid

from sqlalchemy import Column, ForeignKey, Integer, String, TIMESTAMP, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.sql import func

from app.database import Base


class Budget(Base):
    """A maintenance budget for one outlet in one month (Tier 6.3). Spend is
    computed from work orders + purchase orders, not stored here."""

    __tablename__ = "budgets"
    __table_args__ = (UniqueConstraint("outlet_id", "period", name="uq_budget_outlet_period"),)

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    outlet_id = Column(UUID(as_uuid=True), ForeignKey("outlets.id", ondelete="CASCADE"), nullable=False)
    outlet = Column(String(200), nullable=True)     # denormalized name
    period = Column(String(7), nullable=False)      # 'YYYY-MM'
    amount = Column(Integer, nullable=False, default=0)   # IDR
    created_at = Column(TIMESTAMP(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(TIMESTAMP(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)
