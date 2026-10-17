"""Onboarding a new company (Todo-Pilot §24 / UX-11).

The first admin sees what is left to set up, in this order: outlets → users &
roles → categories & PICs → assets & QR → preventive maintenance → daily
checklists → approval policy. A step is done when the data exists, so the checklist cannot drift
from reality. Optional demo data fills an empty company for a sales demo.

Every query runs under the caller's tenant (TenantScoped), so another
company's data never counts.
"""

from __future__ import annotations

from typing import Any, Dict, List

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.models.approval_policy import ApprovalPolicy
from app.models.asset import Asset
from app.models.category import Category
from app.models.issue import Issue
from app.models.ops_checklist import OpsChecklistTemplate
from app.models.outlet import Outlet
from app.models.pic import PIC
from app.models.pm_schedule import PMSchedule
from app.models.user import User
from app.services.audit_service import write_audit
from app.services.category_service import system_category

DEMO = "[Demo] "


def _count(db: Session, model, *where) -> int:
    return db.query(model).filter(*where).count()


def status(db: Session, user_id=None) -> Dict[str, Any]:
    outlets = _count(db, Outlet, Outlet.deleted_at.is_(None))
    users = _count(db, User, User.is_active.is_(True))
    categories = _count(db, Category, Category.deleted_at.is_(None))
    pics = _count(db, PIC, PIC.deleted_at.is_(None))
    assets = _count(db, Asset)
    pm = _count(db, PMSchedule, PMSchedule.deleted_at.is_(None), PMSchedule.is_active.is_(True))
    policies = _count(db, ApprovalPolicy, ApprovalPolicy.deleted_at.is_(None), ApprovalPolicy.is_active.is_(True))
    thresholds = _count(db, Outlet, Outlet.deleted_at.is_(None), Outlet.approval_threshold.isnot(None))
    ops_templates = _count(db, OpsChecklistTemplate, OpsChecklistTemplate.is_active.is_(True))

    steps: List[Dict[str, Any]] = [
        {"key": "outlets", "done": outlets > 0, "count": outlets},
        # The first admin alone is not a team yet.
        {"key": "users", "done": users > 1, "count": users},
        {"key": "masterData", "done": categories > 0 and pics > 0, "count": categories + pics},
        # Every asset gets its QR sticker token on creation.
        {"key": "assets", "done": assets > 0, "count": assets},
        {"key": "pm", "done": pm > 0, "count": pm},
        # Daily checklists — a built-in set per outlet type is one click (Todo-Ops-Checklist Fase 4).
        {"key": "opsChecklist", "done": ops_templates > 0, "count": ops_templates},
        {"key": "approvalPolicy", "done": policies > 0 or thresholds > 0, "count": policies + thresholds},
    ]
    me = db.query(User).filter(User.id == user_id).first() if user_id else None
    return {
        "steps": steps,
        "complete": all(s["done"] for s in steps),
        "demoAvailable": _count(db, Issue) == 0,
        # "Hide" on the card — kept per user, across devices
        "dismissed": bool(((me.preferences if me else None) or {}).get("onboardingDismissed")),
    }


def create_demo_data(db: Session, performed_by: str) -> Dict[str, Any]:
    """Sample outlet, asset and issues for a company that has no issues yet.

    Refuses (409) once real work exists, so it can never mix with live data.
    Everything is named "[Demo] …" so it is easy to recognise and cancel.
    """
    from app.routers.assets import _next_asset_number
    from app.schemas.issue import CreateIssueRequest
    from app.services import issue_service

    if _count(db, Issue) > 0:
        raise HTTPException(status_code=409, detail="Demo data is only for a company without issues.")

    outlet = db.query(Outlet).filter(Outlet.deleted_at.is_(None)).order_by(Outlet.name).first()
    if outlet is None:
        outlet = Outlet(name=f"{DEMO}Outlet", code="DEMO", status="operational")
        db.add(outlet)
        db.flush()

    asset = Asset(number=_next_asset_number(db), name=f"{DEMO}Freezer dapur", category="Refrigeration",
                  outlet=outlet.name, outlet_id=outlet.id, status="operational")
    db.add(asset)
    db.flush()

    def _cat(key: str) -> str:
        return str(system_category(db, key).id)

    samples = [
        dict(title=f"{DEMO}Freezer tidak dingin", categoryId=_cat("maintenance"), priority="high",
             description="Suhu freezer naik ke -5°C sejak pagi.", generateTask=False,
             generateWorkOrder=True, assetId=str(asset.id), estimatedCost=750_000),
        dict(title=f"{DEMO}Beli mixer baru", categoryId=_cat("procurement"), priority="medium",
             description="Mixer lama sering mati.", generateTask=True, generateApproval=True,
             approvalAmount=4_500_000),
        dict(title=f"{DEMO}Tamu mengeluh pesanan terlambat", categoryId=_cat("guest_service"), priority="medium",
             description="Pesanan meja 7 datang 40 menit.", generateTask=True),
    ]
    created = []
    for s in samples:
        req = CreateIssueRequest(outlet=outlet.name, assignee="Unassigned", **s)
        created.append(issue_service.create_issue(db, req, commit=False))

    write_audit(db, table_name="companies", record_id=str(outlet.company_id), action="demo_data",
                new_value={"issues": [c.number for c in created], "asset": asset.number}, performed_by=performed_by)
    db.commit()
    return {"issues": [c.number for c in created], "asset": asset.number, "outlet": outlet.name}
