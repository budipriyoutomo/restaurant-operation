from typing import Optional

from pydantic import BaseModel


class AssignmentRuleResponse(BaseModel):
    id: str
    categoryId: str
    categoryName: str
    outletId: Optional[str] = None      # null = every outlet
    outlet: Optional[str] = None
    assigneeUserId: str
    assigneeName: str
    assigneeActive: bool                # false → the rule is skipped (UI warns)


class CreateAssignmentRuleRequest(BaseModel):
    categoryId: str
    outletId: Optional[str] = None
    assigneeUserId: str


class UpdateAssignmentRuleRequest(BaseModel):
    categoryId: Optional[str] = None
    outletId: Optional[str] = None      # send null to make it an all-outlets rule
    assigneeUserId: Optional[str] = None
