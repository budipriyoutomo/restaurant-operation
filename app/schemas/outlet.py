from typing import Optional
from pydantic import BaseModel


class OutletResponse(BaseModel):
    id: str
    name: str
    code: str
    status: str


class CreateOutletRequest(BaseModel):
    name: str
    code: str
    status: str = "operational"


class UpdateOutletRequest(BaseModel):
    name: Optional[str] = None
    code: Optional[str] = None
    status: Optional[str] = None
