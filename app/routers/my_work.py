"""GET /api/my-work — "Pekerjaan Saya" (Todo-Pilot §19 / UX-6)."""

from typing import Any, Dict

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.database import get_db
from app.services import my_work_service
from app.services.auth_service import UserResponse, get_current_user

router = APIRouter(prefix="/api/my-work", tags=["my-work"])


@router.get("")
def get_my_work(
    limit: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db),
    current_user: UserResponse = Depends(get_current_user),
) -> Dict[str, Any]:
    """What is waiting for me: approvals I may decide, my work orders and tasks,
    issues of my outlets ready to close, guest complaints awaiting a response.
    A section is null when my role cannot open that module."""
    return my_work_service.my_work(db, current_user, limit)
