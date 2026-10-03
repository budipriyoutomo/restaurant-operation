"""Platform admin API — manage customer companies (Todo-Pilot §11).

Only users with is_platform_admin. Company admins get 403: being an admin of
one company says nothing about the platform.
"""

from typing import List

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.database import get_db
from app.services import platform_service as svc
from app.services.auth_service import UserResponse, get_current_user

router = APIRouter(prefix="/api/platform", tags=["platform"])


def require_platform_admin(current_user: UserResponse = Depends(get_current_user)) -> UserResponse:
    if not current_user.is_platform_admin:
        raise HTTPException(status_code=403, detail="Platform administrators only")
    return current_user


@router.get("/companies", response_model=List[svc.CompanyResponse])
def list_companies(db: Session = Depends(get_db), _: UserResponse = Depends(require_platform_admin)):
    return svc.list_companies(db)


@router.post("/companies", response_model=svc.CreateCompanyResponse, status_code=201)
def create_company(req: svc.CreateCompanyRequest, db: Session = Depends(get_db),
                   me: UserResponse = Depends(require_platform_admin)):
    return svc.create_company(db, req, performed_by=me.email)


@router.patch("/companies/{company_id}", response_model=svc.CompanyResponse)
def update_company(company_id: str, req: svc.UpdateCompanyRequest, db: Session = Depends(get_db),
                   me: UserResponse = Depends(require_platform_admin)):
    return svc.update_company(db, company_id, req, performed_by=me.email)
