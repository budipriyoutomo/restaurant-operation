from typing import List

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.database import get_db
from app.services import role_service
from app.services.auth_service import UserResponse, get_current_user, require_permission
from app.services.role_service import (
    CreateRoleRequest,
    ModuleResponse,
    RoleResponse,
    UpdateRoleRequest,
)

router = APIRouter(prefix="/api/roles", tags=["roles"])


@router.get("/modules", response_model=List[ModuleResponse])
def list_modules(_: UserResponse = Depends(get_current_user)):
    """The modules a role can be granted access to."""
    return role_service.list_modules()


@router.get("", response_model=List[RoleResponse])
def list_roles(
    db: Session = Depends(get_db),
    _: UserResponse = Depends(require_permission("users", "view", ("approvals", "manage"))),
):
    return role_service.list_roles(db)


@router.post("", response_model=RoleResponse, status_code=201)
def create_role(
    req: CreateRoleRequest,
    db: Session = Depends(get_db),
    caller: UserResponse = Depends(require_permission("users", "manage")),
):
    return role_service.create_role(db, req, caller)


@router.patch("/{key}", response_model=RoleResponse)
def update_role(
    key: str,
    req: UpdateRoleRequest,
    db: Session = Depends(get_db),
    caller: UserResponse = Depends(require_permission("users", "manage")),
):
    return role_service.update_role(db, key, req, caller)


@router.delete("/{key}", status_code=204)
def delete_role(
    key: str,
    db: Session = Depends(get_db),
    _: UserResponse = Depends(require_permission("users", "manage")),
):
    role_service.delete_role(db, key)
