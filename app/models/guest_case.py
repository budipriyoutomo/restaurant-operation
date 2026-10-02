"""Guest case — the guest side of a Guest Service issue (Todo-Pilot §8, migration 037)."""

import uuid

from sqlalchemy import BigInteger, CheckConstraint, Column, ForeignKey, String, Text, TIMESTAMP
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from app.database import Base


class GuestCase(Base):
    __tablename__ = "guest_cases"
    __table_args__ = (
        CheckConstraint("channel IN ('walk-in', 'phone', 'google-review', 'instagram', 'whatsapp', 'other')",
                        name="guest_cases_channel_check"),
        CheckConstraint("compensation_type IN ('none', 'discount', 'free-item', 'voucher', 'refund', 'other')",
                        name="guest_cases_compensation_type_check"),
        CheckConstraint("compensation_value >= 0", name="guest_cases_compensation_value_check"),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    issue_id = Column(UUID(as_uuid=True), ForeignKey("issues.id", ondelete="CASCADE"), nullable=False, unique=True)
    # Denormalised from the Issue so outlet scoping works on this table directly.
    outlet = Column(String(200), nullable=False, default="", server_default="")
    outlet_id = Column(UUID(as_uuid=True), ForeignKey("outlets.id", ondelete="RESTRICT"), nullable=True)
    guest_name = Column(String(200), nullable=False, default="", server_default="")
    guest_contact = Column(String(200), nullable=False, default="", server_default="")
    channel = Column(String(20), nullable=False, default="other", server_default="other")
    reported_at = Column(TIMESTAMP(timezone=True), nullable=False, server_default=func.now())
    first_response_at = Column(TIMESTAMP(timezone=True), nullable=True)
    first_response_by = Column(String(200), nullable=False, default="", server_default="")
    first_response_note = Column(Text, nullable=False, default="", server_default="")
    resolved_at = Column(TIMESTAMP(timezone=True), nullable=True)
    compensation_type = Column(String(20), nullable=False, default="none", server_default="none")
    compensation_value = Column(BigInteger, nullable=False, default=0, server_default="0")   # integer IDR
    currency = Column(String(3), nullable=False, default="IDR", server_default="IDR")
    compensation_note = Column(Text, nullable=False, default="", server_default="")
    created_at = Column(TIMESTAMP(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(TIMESTAMP(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)

    issue = relationship("Issue", back_populates="guest_case")
