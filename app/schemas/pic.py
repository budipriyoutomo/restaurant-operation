from typing import Optional, List
from pydantic import BaseModel


class PICResponse(BaseModel):
    id: str
    name: str
    email: str
    phone: str
    department: str
    categories: List[str]  # list of category UUIDs


class CreatePICRequest(BaseModel):
    name: str
    email: str
    phone: str
    department: str
    categories: List[str]  # category UUIDs


class UpdatePICRequest(BaseModel):
    name: Optional[str] = None
    email: Optional[str] = None
    phone: Optional[str] = None
    department: Optional[str] = None
    categories: Optional[List[str]] = None
