"""Platform administration — the SaaS operator manages customer companies (Todo-Pilot §11).

Platform admins (users.is_platform_admin, no company) create, rename and
(de)activate companies. They never read a company's business data: their
requests carry no company, so the tenant filter refuses it (403).

Creating a company is one transaction: company, default roles, an optional
first outlet and the first admin user — so the customer can sign in at once,
and a failure leaves nothing half-made.
"""

from __future__ import annotations

import re
import secrets
import uuid
from datetime import datetime
from typing import List, Optional

from fastapi import HTTPException
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.core.tenancy import bypass_tenant, tenant
from app.models.company import Company
from app.models.outlet import Outlet
from app.models.user import User
from app.services.audit_service import write_audit
from app.services.auth_service import hash_password
from app.services.role_service import ensure_default_roles

_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class CompanyResponse(BaseModel):
    id: str
    name: str
    slug: str
    is_active: bool
    created_at: Optional[datetime] = None
    user_count: int = 0
    outlet_count: int = 0


class CreateCompanyRequest(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    admin_name: str = Field(min_length=1, max_length=200)
    admin_email: str = Field(min_length=3, max_length=200)
    admin_password: Optional[str] = Field(default=None, min_length=10, max_length=200)
    outlet_name: Optional[str] = Field(default=None, max_length=200)
    outlet_code: Optional[str] = Field(default=None, max_length=10)

    @field_validator("name", "admin_name")
    @classmethod
    def _not_blank(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("must not be blank")
        return v.strip()

    @field_validator("admin_email")
    @classmethod
    def _email(cls, v: str) -> str:
        v = v.strip().lower()
        if not _EMAIL.match(v):
            raise ValueError("not a valid email address")
        return v


class CreateCompanyResponse(BaseModel):
    company: CompanyResponse
    admin_email: str
    admin_password: Optional[str] = None     # only when generated — shown once, never stored in clear


class UpdateCompanyRequest(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=200)
    is_active: Optional[bool] = None


def slugify(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    return slug[:80] or "company"


def _counts(db: Session, company_ids) -> tuple[dict, dict]:
    users = dict(db.query(User.company_id, func.count(User.id))
                 .filter(User.company_id.in_(company_ids)).group_by(User.company_id).all())
    outlets = dict(db.query(Outlet.company_id, func.count(Outlet.id))
                   .filter(Outlet.company_id.in_(company_ids), Outlet.deleted_at.is_(None))
                   .group_by(Outlet.company_id).all())
    return users, outlets


def _to_response(c: Company, users: dict, outlets: dict) -> CompanyResponse:
    return CompanyResponse(id=str(c.id), name=c.name, slug=c.slug, is_active=c.is_active, created_at=c.created_at,
                           user_count=users.get(c.id, 0), outlet_count=outlets.get(c.id, 0))


def list_companies(db: Session) -> List[CompanyResponse]:
    with bypass_tenant(db):
        companies = db.query(Company).order_by(Company.created_at, Company.name).all()
        users, outlets = _counts(db, [c.id for c in companies])
        return [_to_response(c, users, outlets) for c in companies]


def create_company(db: Session, req: CreateCompanyRequest, performed_by: str) -> CreateCompanyResponse:
    slug = slugify(req.name)
    with bypass_tenant(db):
        if db.query(Company).filter(Company.slug == slug).first():
            raise HTTPException(status_code=409, detail=f"A company named like '{req.name}' already exists ({slug})")
        if db.query(User).filter(func.lower(User.email) == req.admin_email).first():
            raise HTTPException(status_code=409, detail=f"Email '{req.admin_email}' is already registered")
        company = Company(id=uuid.uuid4(), name=req.name, slug=slug, is_active=True)
        db.add(company)
        db.flush()

    password = req.admin_password or secrets.token_urlsafe(12)
    with tenant(db, company.id):
        ensure_default_roles(db)
        if req.outlet_name and req.outlet_name.strip():
            code = (req.outlet_code or slugify(req.outlet_name).replace("-", "")[:10]).upper()
            db.add(Outlet(name=req.outlet_name.strip(), code=code, status="operational"))
        db.add(User(email=req.admin_email, name=req.admin_name, password_hash=hash_password(password), role="admin"))
        write_audit(db, table_name="companies", record_id=str(company.id), action="company_created",
                    new_value={"name": company.name, "slug": slug, "admin_email": req.admin_email},
                    performed_by=performed_by)
        db.commit()

    with bypass_tenant(db):
        db.refresh(company)
        users, outlets = _counts(db, [company.id])
        return CreateCompanyResponse(company=_to_response(company, users, outlets), admin_email=req.admin_email,
                                     admin_password=None if req.admin_password else password)


def update_company(db: Session, company_id: str, req: UpdateCompanyRequest, performed_by: str) -> CompanyResponse:
    try:
        cid = uuid.UUID(company_id)
    except ValueError:
        raise HTTPException(status_code=404, detail="Company not found")
    with bypass_tenant(db):
        company = db.get(Company, cid)
    if company is None:
        raise HTTPException(status_code=404, detail="Company not found")

    old = {"name": company.name, "is_active": company.is_active}
    if req.name is not None:
        company.name = req.name.strip()
    if req.is_active is not None:
        company.is_active = req.is_active
    new = {"name": company.name, "is_active": company.is_active}
    with tenant(db, company.id):              # the audit row belongs to that company
        if new != old:
            write_audit(db, table_name="companies", record_id=str(company.id), action="company_updated",
                        old_value=old, new_value=new, performed_by=performed_by)
        db.commit()
    with bypass_tenant(db):
        db.refresh(company)
        users, outlets = _counts(db, [company.id])
        return _to_response(company, users, outlets)


def create_platform_admin(db: Session, email: str, name: str, password: Optional[str] = None) -> tuple:
    """Bootstrap an operator account (scripts/create_platform_admin.py). Returns
    (user, password); the password is generated when not given."""
    email = email.strip().lower()
    password = password or secrets.token_urlsafe(12)
    with bypass_tenant(db):
        if db.query(User).filter(func.lower(User.email) == email).first():
            raise ValueError(f"Email {email!r} is already registered")
        user = User(email=email, name=name, password_hash=hash_password(password), role="staff",
                    company_id=None, is_platform_admin=True)
        db.add(user)
        db.commit()
        db.refresh(user)
    return user, password
