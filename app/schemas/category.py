from typing import Literal, Optional
from pydantic import BaseModel, Field

CategoryModule = Literal["maintenance", "it_support", "compliance", "guest_service", "asset_purchase", "general"]
CategoryApprovalType = Literal["procurement", "marketing", "training", "asset-purchase", "maintenance"]


class CategoryResponse(BaseModel):
    id: str
    name: str
    description: str
    type: str                       # legacy (operations | maintenance), derived from module
    module: str
    systemKey: Optional[str] = None
    isSystem: bool = False
    isActive: bool = True
    defaultGenerateTask: bool = True
    defaultGenerateApproval: bool = False
    approvalType: Optional[str] = None
    icon: Optional[str] = None


class CreateCategoryRequest(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    description: str = ""
    type: Optional[Literal["operations", "maintenance"]] = None   # legacy; used only when module is absent
    module: Optional[CategoryModule] = None
    defaultGenerateTask: bool = True
    defaultGenerateApproval: bool = False
    approvalType: Optional[CategoryApprovalType] = None
    icon: Optional[str] = Field(default=None, max_length=40)


class UpdateCategoryRequest(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=200)
    description: Optional[str] = None
    type: Optional[Literal["operations", "maintenance"]] = None   # legacy; ignored when module is given
    module: Optional[CategoryModule] = None
    isActive: Optional[bool] = None
    defaultGenerateTask: Optional[bool] = None
    defaultGenerateApproval: Optional[bool] = None
    approvalType: Optional[CategoryApprovalType] = None
    icon: Optional[str] = Field(default=None, max_length=40)
