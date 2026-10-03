import uuid
from sqlalchemy import Column, String, Date, Text, Integer, Enum as SAEnum, TIMESTAMP, ForeignKey, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from app.core.tenancy import TenantScoped, company_key_column
from app.database import Base
from app.models.enums import PriorityEnum, TaskStatusEnum


def _sa_enum(py_enum, pg_name):
    return SAEnum(
        py_enum,
        values_callable=lambda x: [e.value for e in x],
        name=pg_name,
        create_type=False,
    )


class Task(TenantScoped, Base):
    __tablename__ = "tasks"
    __table_args__ = (UniqueConstraint("company_id", "number", name="uq_tasks_company_number"),)

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    issue_id = Column(UUID(as_uuid=True), ForeignKey("issues.id", ondelete="CASCADE"), nullable=False)
    issue_number = Column(String(30), nullable=False)       # denormalized (FR-10)
    number = Column(String(30), nullable=False)  # TSK-2026-00001
    title = Column(String(500), nullable=False)
    description = Column(Text, default="")
    status = Column(_sa_enum(TaskStatusEnum, "task_status"), nullable=False, default=TaskStatusEnum.open)
    priority = Column(_sa_enum(PriorityEnum, "priority"), nullable=False, default=PriorityEnum.medium)
    assignee = Column(String(200), default="Unassigned")
    due_date = Column(Date)
    outlet = Column(String(200), default="")
    # Real FK alongside the denormalised name (migration 024). Nullable:
    # NULL means "not tied to one outlet" (shared / All Outlets).
    outlet_id = Column(UUID(as_uuid=True), ForeignKey("outlets.id", ondelete="RESTRICT"), nullable=True)
    created_at = Column(TIMESTAMP(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(TIMESTAMP(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)

    issue = relationship("Issue", back_populates="tasks")


class TaskNumberSequence(TenantScoped, Base):
    __tablename__ = "task_number_sequences"

    company_id = company_key_column()
    year = Column(Integer, primary_key=True)
    last_seq = Column(Integer, nullable=False, default=0)
