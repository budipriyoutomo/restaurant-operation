from typing import Optional
from pydantic import BaseModel


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
