import uuid
from sqlalchemy import Column, String, Date, Text, Integer, Enum as SAEnum, TIMESTAMP, ForeignKey
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from app.database import Base
from app.models.enums import ApprovalTypeEnum, ApprovalStatusEnum


def _sa_enum(py_enum, pg_name):
    return SAEnum(
        py_enum,
        values_callable=lambda x: [e.value for e in x],
        name=pg_name,
        create_type=False,
    )


class ApprovalRequest(Base):
    __tablename__ = "approval_requests"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    issue_id = Column(
        UUID(as_uuid=True),
        ForeignKey("issues.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,  # enforces max one Approval per Issue (PRD §6)
    )
    issue_number = Column(String(30), nullable=False)       # denormalized (FR-15)
    number = Column(String(30), nullable=False, unique=True)  # APR-2026-00001
    title = Column(String(500), nullable=False)
    type = Column(_sa_enum(ApprovalTypeEnum, "approval_type"), nullable=False)
    description = Column(Text, default="")
    requester = Column(String(200), default="")
    outlet = Column(String(200), default="")
    requested_date = Column(Date)
    amount = Column(String(100))                            # stored as-is e.g. "RM 45,000"
    status = Column(_sa_enum(ApprovalStatusEnum, "approval_status"), nullable=False, default=ApprovalStatusEnum.pending)
    decided_at = Column(TIMESTAMP(timezone=True))
    decided_by = Column(String(200))
    decision_note = Column(Text)
    created_at = Column(TIMESTAMP(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(TIMESTAMP(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)

    issue = relationship("Issue", back_populates="approval")


class ApprovalNumberSequence(Base):
    __tablename__ = "approval_number_sequences"

    year = Column(Integer, primary_key=True)
    last_seq = Column(Integer, nullable=False, default=0)
