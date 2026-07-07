from typing import List, Optional

from pydantic import BaseModel, Field


class PolicyStep(BaseModel):
    """One step in a policy's approval chain."""
    order: int = Field(ge=1)
    role: str                       # staff | manager | admin


class ApprovalPolicyResponse(BaseModel):
    """Shape matches the frontend ApprovalPolicy interface in lib/types.ts exactly."""
    id: str
    approvalType: str
    minAmount: Optional[int] = None       # IDR integer
    maxAmount: Optional[int] = None       # IDR integer
    steps: List[PolicyStep] = []
    outlet: Optional[str] = None
    isActive: bool
    createdAt: str


class CreateApprovalPolicyRequest(BaseModel):
    approvalType: str
    minAmount: Optional[int] = None
    maxAmount: Optional[int] = None
    steps: List[PolicyStep]
    outlet: Optional[str] = None
    isActive: bool = True


class UpdateApprovalPolicyRequest(BaseModel):
    approvalType: Optional[str] = None
    minAmount: Optional[int] = None
    maxAmount: Optional[int] = None
    steps: Optional[List[PolicyStep]] = None
    outlet: Optional[str] = None
    isActive: Optional[bool] = None
