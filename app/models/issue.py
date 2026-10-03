import uuid
from sqlalchemy import ForeignKey, Column, String, Date, Text, Integer, Enum as SAEnum, TIMESTAMP, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from app.core.tenancy import TenantScoped, company_key_column
from app.database import Base
from app.models.enums import IssueCategoryEnum, PriorityEnum, IssueStatusEnum


def _sa_enum(py_enum, pg_name):
    """Build a SQLAlchemy Enum that stores .value strings in PostgreSQL.
    create_type=False because types are created explicitly in Alembic migrations."""
    return SAEnum(
        py_enum,
        values_callable=lambda x: [e.value for e in x],
        name=pg_name,
        create_type=False,
    )


class Issue(TenantScoped, Base):
    __tablename__ = "issues"
    __table_args__ = (UniqueConstraint("company_id", "number", name="uq_issues_company_number"),)

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    number = Column(String(30), nullable=False)     # ISS-2026-00001
    title = Column(String(500), nullable=False)
    description = Column(Text, default="")
    outlet = Column(String(200), nullable=False)
    # Real FK alongside the denormalised name (migration 024). Nullable:
    # NULL means "not tied to one outlet" (shared / All Outlets).
    outlet_id = Column(UUID(as_uuid=True), ForeignKey("outlets.id", ondelete="RESTRICT"), nullable=True)
    category = Column(_sa_enum(IssueCategoryEnum, "issue_category"), nullable=False)
    priority = Column(_sa_enum(PriorityEnum, "priority"), nullable=False, default=PriorityEnum.medium)
    status = Column(_sa_enum(IssueStatusEnum, "issue_status"), nullable=False, default=IssueStatusEnum.open)
    assignee = Column(String(200), default="Unassigned")
    due_date = Column(Date)
    created_at = Column(TIMESTAMP(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(TIMESTAMP(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)

    tasks = relationship(
        "Task",
        back_populates="issue",
        cascade="all, delete-orphan",
        lazy="selectin",
    )
    approval = relationship(
        "ApprovalRequest",
        back_populates="issue",
        cascade="all, delete-orphan",
        uselist=False,
        lazy="selectin",
    )
    # Guest side of a "Guest Service" issue (Todo-Pilot §8); None for other categories.
    guest_case = relationship(
        "GuestCase",
        back_populates="issue",
        cascade="all, delete-orphan",
        uselist=False,
        lazy="selectin",
    )
    # Read-only link to auto-generated work orders (WorkOrder.issue_id → SET NULL on delete,
    # so no cascade here). Enables IssueResponse.workOrderId to be populated.
    work_orders = relationship(
        "WorkOrder",
        foreign_keys="WorkOrder.issue_id",
        viewonly=True,
        lazy="selectin",
    )


class IssueNumberSequence(TenantScoped, Base):
    __tablename__ = "issue_number_sequences"

    company_id = company_key_column()
    year = Column(Integer, primary_key=True)
    last_seq = Column(Integer, nullable=False, default=0)
