import uuid
from sqlalchemy import Column, String, Date, Text, Integer, Enum as SAEnum, TIMESTAMP, ForeignKey
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from app.database import Base
from app.models.enums import PriorityEnum, TaskStatusEnum


def _sa_enum(py_enum, pg_name):
    return SAEnum(
        py_enum,
        values_callable=lambda x: [e.value for e in x],
        name=pg_name,
        create_type=False,
    )


class Task(Base):
    __tablename__ = "tasks"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    issue_id = Column(UUID(as_uuid=True), ForeignKey("issues.id", ondelete="CASCADE"), nullable=False)
    issue_number = Column(String(30), nullable=False)       # denormalized (FR-10)
    number = Column(String(30), nullable=False, unique=True)  # TSK-2026-00001
    title = Column(String(500), nullable=False)
    description = Column(Text, default="")
    status = Column(_sa_enum(TaskStatusEnum, "task_status"), nullable=False, default=TaskStatusEnum.open)
    priority = Column(_sa_enum(PriorityEnum, "priority"), nullable=False, default=PriorityEnum.medium)
    assignee = Column(String(200), default="Unassigned")
    due_date = Column(Date)
    outlet = Column(String(200), default="")
    created_at = Column(TIMESTAMP(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(TIMESTAMP(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)

    issue = relationship("Issue", back_populates="tasks")


class TaskNumberSequence(Base):
    __tablename__ = "task_number_sequences"

    year = Column(Integer, primary_key=True)
    last_seq = Column(Integer, nullable=False, default=0)
