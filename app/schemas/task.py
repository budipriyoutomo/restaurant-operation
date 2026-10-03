from typing import Optional
from pydantic import BaseModel, field_validator

from app.models.enums import PriorityEnum, TaskStatusEnum
from app.schemas.validators import in_enum


class TaskResponse(BaseModel):
    """Shape matches the frontend Task interface in lib/types.ts exactly."""
    id: str
    number: str
    title: str
    description: str
    status: str
    priority: str
    assignee: str
    dueDate: Optional[str] = None
    outlet: str
    issueId: str
    issueNumber: str


class UpdateTaskRequest(BaseModel):
    status: Optional[str] = None
    assignee: Optional[str] = None
    priority: Optional[str] = None

    @field_validator("status")
    @classmethod
    def check_status(cls, v: Optional[str]) -> Optional[str]:
        return in_enum(v, TaskStatusEnum)

    @field_validator("priority")
    @classmethod
    def check_priority(cls, v: Optional[str]) -> Optional[str]:
        return in_enum(v, PriorityEnum)
