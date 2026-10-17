from typing import Optional, List
from pydantic import BaseModel


class PICResponse(BaseModel):
    id: str
    name: str
    email: str
    phone: str
    department: str
    categories: List[str]  # list of category UUIDs
    userId: Optional[str] = None     # linked user account (P2) — routes the PIC's categories to them
    userName: Optional[str] = None


class CreatePICRequest(BaseModel):
    name: str
    email: str
    phone: str
    department: str
    categories: List[str]  # category UUIDs
    userId: Optional[str] = None


class UpdatePICRequest(BaseModel):
    name: Optional[str] = None
    email: Optional[str] = None
    phone: Optional[str] = None
    department: Optional[str] = None
    categories: Optional[List[str]] = None
    userId: Optional[str] = None     # send null to unlink
