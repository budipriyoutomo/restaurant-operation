from typing import Optional
from pydantic import BaseModel


class CategoryResponse(BaseModel):
    id: str
    name: str
    description: str
    type: str


class CreateCategoryRequest(BaseModel):
    name: str
    description: str = ""
    type: str = "operations"


class UpdateCategoryRequest(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    type: Optional[str] = None
