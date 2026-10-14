"""Onboarding checklist and demo data for a new company (Todo-Pilot §24 / UX-11)."""

from typing import Any, Dict

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.database import get_db
from app.services import onboarding_service
from app.services.auth_service import UserResponse, require_permission

router = APIRouter(prefix="/api/onboarding", tags=["onboarding"])

# Setting the company up is the job of whoever manages its users.
_SETUP = require_permission("users", "manage")


@router.get("")
def get_onboarding(db: Session = Depends(get_db), current_user: UserResponse = Depends(_SETUP)) -> Dict[str, Any]:
    """What is left to set up, step by step; done = the data exists."""
    return onboarding_service.status(db, current_user.id)


@router.post("/demo-data", status_code=201)
def create_demo_data(db: Session = Depends(get_db), current_user: UserResponse = Depends(_SETUP)) -> Dict[str, Any]:
    """Fill an empty company with "[Demo]" records; 409 once it has issues."""
    return onboarding_service.create_demo_data(db, current_user.email)
