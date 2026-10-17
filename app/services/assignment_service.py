"""Assignee routing — DB side (Todo-Ops-Checklist P2).

  route_assignee   the user a new, unassigned Issue goes to (or None), using the
                   pure order in assignment_rules: (category, outlet) rule →
                   (category, all outlets) rule → PIC of the category with a user
  rule CRUD        /api/issue-assignment-rules (master data)
"""

from __future__ import annotations

import uuid
from typing import List, Optional

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.models.assignment_rule import IssueAssignmentRule
from app.models.category import Category
from app.models.outlet import Outlet
from app.models.pic import PIC
from app.models.user import User
from app.schemas.assignment_rule import AssignmentRuleResponse, CreateAssignmentRuleRequest, UpdateAssignmentRuleRequest
from app.services.assignee_service import can_work_at
from app.services.assignment_rules import PicLink, Rule, candidate_user_ids, pick_assignee
from app.services.audit_service import write_audit


def _uuid(value, what: str) -> uuid.UUID:
    try:
        return uuid.UUID(str(value))
    except (ValueError, TypeError):
        raise HTTPException(status_code=422, detail=f"Unknown {what}")


# ---------------------------------------------------------------------------
# Routing
# ---------------------------------------------------------------------------

def route_assignee(db: Session, category_id: uuid.UUID, outlet_id: Optional[uuid.UUID]) -> Optional[uuid.UUID]:
    rules = [Rule(r.category_id, r.outlet_id, r.assignee_user_id)
             for r in db.query(IssueAssignmentRule).filter(IssueAssignmentRule.category_id == category_id)]
    pics = [PicLink(p.name, p.user_id, {c.id for c in p.categories})
            for p in db.query(PIC).filter(PIC.deleted_at.is_(None), PIC.user_id.isnot(None))]
    candidates = candidate_user_ids(rules, pics, category_id, outlet_id)
    if not candidates:
        return None
    users = {u.id: u for u in db.query(User).filter(User.id.in_(candidates))}

    def eligible(uid: uuid.UUID) -> bool:
        u = users.get(uid)
        return u is not None and bool(u.is_active) and can_work_at(db, u, outlet_id)

    return pick_assignee(candidates, eligible)


# ---------------------------------------------------------------------------
# Validation shared with PICs
# ---------------------------------------------------------------------------

def active_user(db: Session, user_id) -> User:
    """An active user of the current company (others are invisible) or 422."""
    user = db.query(User).filter(User.id == _uuid(user_id, "user")).first()
    if user is None or not user.is_active:
        raise HTTPException(status_code=422, detail="Unknown or inactive user")
    return user


# ---------------------------------------------------------------------------
# Rule CRUD
# ---------------------------------------------------------------------------

def to_response(r: IssueAssignmentRule) -> AssignmentRuleResponse:
    return AssignmentRuleResponse(
        id=str(r.id), categoryId=str(r.category_id), categoryName=r.category.name if r.category else "",
        outletId=str(r.outlet_id) if r.outlet_id else None, outlet=r.outlet.name if r.outlet else None,
        assigneeUserId=str(r.assignee_user_id), assigneeName=r.assignee.name if r.assignee else "",
        assigneeActive=bool(r.assignee and r.assignee.is_active),
    )


def _snapshot(r: IssueAssignmentRule) -> dict:
    return {"categoryId": str(r.category_id), "outletId": str(r.outlet_id) if r.outlet_id else None,
            "assigneeUserId": str(r.assignee_user_id)}


def list_rules(db: Session) -> List[AssignmentRuleResponse]:
    rows = db.query(IssueAssignmentRule).all()
    rows.sort(key=lambda r: ((r.category.name if r.category else "").lower(), r.outlet_id is not None,
                             (r.outlet.name if r.outlet else "").lower()))
    return [to_response(r) for r in rows]


def get_rule_or_404(db: Session, rule_id: str) -> IssueAssignmentRule:
    try:
        rid = uuid.UUID(rule_id)
    except ValueError:
        raise HTTPException(status_code=404, detail="Rule not found")
    rule = db.query(IssueAssignmentRule).filter(IssueAssignmentRule.id == rid).first()
    if rule is None:
        raise HTTPException(status_code=404, detail="Rule not found")
    return rule


def _validate(db: Session, category_id, outlet_id, user_id, exclude_id=None):
    cat = db.query(Category).filter(Category.id == _uuid(category_id, "category"),
                                    Category.deleted_at.is_(None)).first()
    if cat is None:
        raise HTTPException(status_code=422, detail="Unknown category")
    outlet = None
    if outlet_id:
        outlet = db.query(Outlet).filter(Outlet.id == _uuid(outlet_id, "outlet"), Outlet.deleted_at.is_(None)).first()
        if outlet is None:
            raise HTTPException(status_code=422, detail="Unknown outlet")
    user = active_user(db, user_id)
    if not can_work_at(db, user, outlet.id if outlet else None):
        raise HTTPException(status_code=422, detail=f"{user.name} has no access to {outlet.name}")
    dup = db.query(IssueAssignmentRule).filter(
        IssueAssignmentRule.category_id == cat.id,
        (IssueAssignmentRule.outlet_id == outlet.id) if outlet else IssueAssignmentRule.outlet_id.is_(None))
    if exclude_id is not None:
        dup = dup.filter(IssueAssignmentRule.id != exclude_id)
    if dup.first() is not None:
        where = outlet.name if outlet else "all outlets"
        raise HTTPException(status_code=409, detail=f"{cat.name} @ {where} already has a rule")
    return cat, outlet, user


def create_rule(db: Session, req: CreateAssignmentRuleRequest, actor: str) -> AssignmentRuleResponse:
    cat, outlet, user = _validate(db, req.categoryId, req.outletId, req.assigneeUserId)
    rule = IssueAssignmentRule(category_id=cat.id, outlet_id=outlet.id if outlet else None, assignee_user_id=user.id)
    db.add(rule)
    db.flush()
    write_audit(db, table_name="issue_assignment_rules", record_id=str(rule.id), action="create",
                new_value=_snapshot(rule), performed_by=actor)
    db.commit()
    db.refresh(rule)
    return to_response(rule)


def update_rule(db: Session, rule: IssueAssignmentRule, req: UpdateAssignmentRuleRequest, actor: str) -> AssignmentRuleResponse:
    old = _snapshot(rule)
    sent = req.model_fields_set
    category_id = req.categoryId if req.categoryId is not None else rule.category_id
    outlet_id = req.outletId if "outletId" in sent else rule.outlet_id
    user_id = req.assigneeUserId if req.assigneeUserId is not None else rule.assignee_user_id
    cat, outlet, user = _validate(db, category_id, outlet_id, user_id, exclude_id=rule.id)
    rule.category_id, rule.outlet_id, rule.assignee_user_id = cat.id, outlet.id if outlet else None, user.id
    write_audit(db, table_name="issue_assignment_rules", record_id=str(rule.id), action="update",
                old_value=old, new_value=_snapshot(rule), performed_by=actor)
    db.commit()
    db.refresh(rule)
    return to_response(rule)


def delete_rule(db: Session, rule: IssueAssignmentRule, actor: str) -> None:
    write_audit(db, table_name="issue_assignment_rules", record_id=str(rule.id), action="delete",
                old_value=_snapshot(rule), performed_by=actor)
    db.delete(rule)
    db.commit()
