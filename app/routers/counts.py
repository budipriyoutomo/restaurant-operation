"""GET /api/counts — menu badges (Todo-Pilot §22 / UX-9)."""

from typing import Dict, Optional

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.database import get_db
from app.services import overview_service
from app.services.auth_service import UserResponse, get_current_user

router = APIRouter(prefix="/api/counts", tags=["counts"])


@router.get("")
def get_counts(db: Session = Depends(get_db), current_user: UserResponse = Depends(get_current_user)) -> Dict[str, Optional[int]]:
    """Issues in progress, active tasks, pending approvals — null where the role cannot open the list."""
    return overview_service.counts(db, current_user)
