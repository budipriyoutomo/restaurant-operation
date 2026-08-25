import uuid

from sqlalchemy import ForeignKey, Boolean, Column, Integer, String, TIMESTAMP, Enum as SAEnum
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.sql import func

from app.database import Base
from app.models.enums import ApprovalTypeEnum


class ApprovalPolicy(Base):
    """A rule that resolves the approval-step chain for a new ApprovalRequest.

    Matching is by `approval_type` + `amount` within [min_amount, max_amount]
    (null bounds = unbounded), optionally scoped to a single `outlet`.
    `steps` is a JSONB list of {"order": int, "role": str}.
    """

    __tablename__ = "approval_policies"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    approval_type = Column(
        SAEnum(ApprovalTypeEnum, values_callable=lambda x: [e.value for e in x],
               name="approval_type", create_type=False),
        nullable=False,
    )
    min_amount = Column(Integer, nullable=True)     # inclusive lower bound (IDR)
    max_amount = Column(Integer, nullable=True)     # inclusive upper bound (IDR)
    steps = Column(JSONB, nullable=False, default=list)
    outlet = Column(String(200), nullable=True)     # null = all outlets
    # Real FK alongside the denormalised name (migration 024). Nullable:
    # NULL means "not tied to one outlet" (shared / All Outlets).
    outlet_id = Column(UUID(as_uuid=True), ForeignKey("outlets.id", ondelete="RESTRICT"), nullable=True)
    is_active = Column(Boolean, nullable=False, default=True)
    created_at = Column(TIMESTAMP(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(TIMESTAMP(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)
    deleted_at = Column(TIMESTAMP(timezone=True), nullable=True)
