"""Issue categories as master data (Todo-Ops-Checklist P1).

  ensure_system_categories  every company has the built-in rows (new company,
                            seed, tests; migration 044 for existing companies)
  resolve_category          the active category an Issue is created with —
                            by id or by name (case-insensitive), 422 otherwise
  system_category           a built-in row by key, for code that raises Issues
                            itself (QA findings, guest cases, demo data)

Rules for system rows: rename and edit defaults freely; never delete,
deactivate, or change module — code depends on them existing (see
app/core/category_defaults.py).
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Optional

from fastapi import HTTPException
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.core.category_defaults import SYSTEM_CATEGORIES, legacy_type, module_from_legacy_type
from app.models.category import Category
from app.models.issue import Issue
from app.schemas.category import CategoryResponse, CreateCategoryRequest, UpdateCategoryRequest
from app.services.audit_service import write_audit


def to_response(c: Category) -> CategoryResponse:
    return CategoryResponse(
        id=str(c.id), name=c.name, description=c.description or "",
        type=legacy_type(c.module), module=c.module,
        systemKey=c.system_key, isSystem=c.system_key is not None, isActive=bool(c.is_active),
        defaultGenerateTask=bool(c.default_generate_task),
        defaultGenerateApproval=bool(c.default_generate_approval),
        approvalType=c.approval_type, icon=c.icon,
    )


def _snapshot(c: Category) -> dict:
    return {"name": c.name, "description": c.description, "module": c.module, "systemKey": c.system_key,
            "isActive": c.is_active, "defaultGenerateTask": c.default_generate_task,
            "defaultGenerateApproval": c.default_generate_approval, "approvalType": c.approval_type,
            "icon": c.icon}


def active_query(db: Session):
    """Categories not deleted (inactive ones included — master data lists them)."""
    return db.query(Category).filter(Category.deleted_at.is_(None))


def _by_name(db: Session, name: str, exclude_id=None) -> Optional[Category]:
    q = active_query(db).filter(func.lower(Category.name) == name.strip().lower())
    if exclude_id is not None:
        q = q.filter(Category.id != exclude_id)
    return q.first()


# ---------------------------------------------------------------------------
# System rows
# ---------------------------------------------------------------------------

def ensure_system_categories(db: Session) -> None:
    """Create any missing system category for the current company. An existing
    row with the default name (e.g. a pre-P1 "Compliance") is adopted rather
    than duplicated. Idempotent; flushes, does not commit."""
    existing = {c.system_key for c in db.query(Category).filter(Category.system_key.isnot(None))}
    for key, d in SYSTEM_CATEGORIES.items():
        if key in existing:
            continue
        row = _by_name(db, d["name"])
        if row is None or row.system_key is not None:
            row = Category(name=d["name"] if row is None else f"{d['name']} ({key})", description=d["description"])
            db.add(row)
        row.system_key = key
        row.module = d["module"]
        row.is_active = True
        row.default_generate_task = d["task"]
        row.default_generate_approval = d["approval"]
        row.approval_type = d["approval_type"]
        row.icon = row.icon or d["icon"]
    db.flush()


def system_category(db: Session, key: str) -> Category:
    if key not in SYSTEM_CATEGORIES:
        raise ValueError(f"Unknown system category {key!r}")
    cat = db.query(Category).filter(Category.system_key == key).first()
    if cat is None:
        ensure_system_categories(db)
        cat = db.query(Category).filter(Category.system_key == key).one()
    return cat


# ---------------------------------------------------------------------------
# Resolving the category of a new Issue
# ---------------------------------------------------------------------------

def resolve_category(db: Session, *, name: Optional[str] = None, category_id: Optional[str] = None) -> Category:
    """The active category for a new Issue. `category_id` wins over `name`.
    Unknown, deleted, inactive, or another company's → 422 (the tenant filter
    makes another company's id simply not found)."""
    cat = None
    if category_id:
        try:
            cid = uuid.UUID(str(category_id))
        except (ValueError, TypeError):
            raise HTTPException(status_code=422, detail=f"categoryId {category_id!r} is not a valid id")
        cat = active_query(db).filter(Category.id == cid).first()
        label = str(category_id)
    elif name and name.strip():
        cat = _by_name(db, name)
        label = name
    else:
        raise HTTPException(status_code=422, detail="category or categoryId is required")
    if cat is None:
        raise HTTPException(status_code=422, detail=f"Unknown category {label!r}")
    if not cat.is_active:
        raise HTTPException(status_code=422, detail=f"Category {cat.name!r} is inactive")
    return cat


# ---------------------------------------------------------------------------
# CRUD (master data)
# ---------------------------------------------------------------------------

def _assert_name_free(db: Session, name: str, exclude_id=None) -> None:
    if _by_name(db, name, exclude_id) is not None:
        raise HTTPException(status_code=409, detail=f"A category named {name.strip()!r} already exists")


def create_category(db: Session, req: CreateCategoryRequest, actor: str) -> CategoryResponse:
    _assert_name_free(db, req.name)
    cat = Category(
        name=req.name.strip(), description=req.description,
        module=req.module or module_from_legacy_type(req.type),
        default_generate_task=req.defaultGenerateTask, default_generate_approval=req.defaultGenerateApproval,
        approval_type=req.approvalType, icon=req.icon, is_active=True,
    )
    db.add(cat)
    db.flush()
    write_audit(db, table_name="categories", record_id=str(cat.id), action="create",
                new_value=_snapshot(cat), performed_by=actor)
    db.commit()
    db.refresh(cat)
    return to_response(cat)


def update_category(db: Session, cat: Category, req: UpdateCategoryRequest, actor: str) -> CategoryResponse:
    old = _snapshot(cat)
    sent = req.model_fields_set
    is_system = cat.system_key is not None

    module = req.module or (module_from_legacy_type(req.type) if req.type is not None else None)
    if module is not None and module != cat.module:
        if is_system:
            raise HTTPException(status_code=409, detail="The module of a system category cannot be changed")
        cat.module = module
    if req.isActive is not None and req.isActive != cat.is_active:
        if is_system and not req.isActive:
            raise HTTPException(status_code=409, detail="A system category cannot be deactivated")
        cat.is_active = req.isActive

    renamed = False
    if req.name is not None and req.name.strip() != cat.name:
        _assert_name_free(db, req.name, exclude_id=cat.id)
        cat.name = req.name.strip()
        renamed = True
    if req.description is not None:
        cat.description = req.description
    if req.defaultGenerateTask is not None:
        cat.default_generate_task = req.defaultGenerateTask
    if req.defaultGenerateApproval is not None:
        cat.default_generate_approval = req.defaultGenerateApproval
    if "approvalType" in sent:
        cat.approval_type = req.approvalType
    if "icon" in sent:
        cat.icon = req.icon

    if renamed:
        # issues.category is the denormalized name — keep it in step (like outlet renames).
        db.query(Issue).filter(Issue.category_id == cat.id).update(
            {Issue.category: cat.name}, synchronize_session="fetch")

    write_audit(db, table_name="categories", record_id=str(cat.id), action="update",
                old_value=old, new_value=_snapshot(cat), performed_by=actor)
    db.commit()
    db.refresh(cat)
    return to_response(cat)


def delete_category(db: Session, cat: Category, actor: str) -> None:
    """Soft delete. Issues keep their category_id and name; a system category is 409."""
    if cat.system_key is not None:
        raise HTTPException(status_code=409, detail="A system category cannot be deleted; rename it instead")
    old = _snapshot(cat)
    cat.deleted_at = datetime.now(timezone.utc)
    cat.is_active = False
    write_audit(db, table_name="categories", record_id=str(cat.id), action="delete",
                old_value=old, performed_by=actor)
    db.commit()
