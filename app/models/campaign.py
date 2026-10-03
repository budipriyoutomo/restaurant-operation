import uuid
from sqlalchemy import BigInteger, CheckConstraint, Enum as SAEnum, ForeignKey, Column, Integer, String, Text, Date, TIMESTAMP
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.sql import func

from app.core.tenancy import TenantScoped
from app.database import Base


class Campaign(TenantScoped, Base):
    __tablename__ = "campaigns"
    __table_args__ = tuple(
        CheckConstraint(f"{c} >= 0", name=f"campaigns_{c}_check")
        for c in ("budget", "actual_cost", "result_transactions", "result_revenue",
                  "baseline_transactions", "baseline_revenue")
    )

    id          = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    title       = Column(String(300), nullable=False)
    # Native Postgres enums in the migrations (see test_unit_model_enums).
    type        = Column(SAEnum("promotion", "event", "social-media", "email", "other",
                                name="campaign_type", create_type=False),
                         nullable=False, default="other")
    description = Column(Text, nullable=True)
    outlet      = Column(String(200), nullable=True)
    # Real FK alongside the denormalised name (migration 024). Nullable:
    # NULL means "not tied to one outlet" (shared / All Outlets).
    outlet_id = Column(UUID(as_uuid=True), ForeignKey("outlets.id", ondelete="RESTRICT"), nullable=True)
    # Integer major units + currency (migration 039, Todo-Pilot §10). Old free
    # text that could not be read is kept in budget_legacy.
    budget        = Column(BigInteger, nullable=True)
    currency      = Column(String(3), nullable=False, default="IDR", server_default="IDR")
    budget_legacy = Column(Text, nullable=True)
    # Entered by hand: spend, results in the campaign period, optional baseline.
    actual_cost           = Column(BigInteger, nullable=True)
    result_transactions   = Column(Integer, nullable=True)
    result_revenue        = Column(BigInteger, nullable=True)
    baseline_transactions = Column(Integer, nullable=True)
    baseline_revenue      = Column(BigInteger, nullable=True)
    start_date  = Column(Date, nullable=True)
    end_date    = Column(Date, nullable=True)
    status      = Column(SAEnum("draft", "active", "completed", "cancelled",
                                name="campaign_status", create_type=False),
                         nullable=False, default="draft")
    pic         = Column(String(200), nullable=True)
    created_at  = Column(TIMESTAMP(timezone=True), server_default=func.now(), nullable=False)
    updated_at  = Column(TIMESTAMP(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)
