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
    assigneeUserId: Optional[str] = None   # Todo-Pilot §17; null = not linked to a user
    dueDate: Optional[str] = None
    outlet: str
    issueId: str
    issueNumber: str
    issueStatus: Optional[str] = None      # a finished Issue locks its tasks (Todo-Pilot §22/§23)


class UpdateTaskRequest(BaseModel):
    status: Optional[str] = None
    assignee: Optional[str] = None
    assigneeUserId: Optional[str] = None   # wins over `assignee`
    priority: Optional[str] = None

    @field_validator("status")
    @classmethod
    def check_status(cls, v: Optional[str]) -> Optional[str]:
        return in_enum(v, TaskStatusEnum)

    @field_validator("priority")
    @classmethod
    def check_priority(cls, v: Optional[str]) -> Optional[str]:
        return in_enum(v, PriorityEnum)
