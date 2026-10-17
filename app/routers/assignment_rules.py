from typing import List

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.database import get_db
from app.schemas.assignment_rule import AssignmentRuleResponse, CreateAssignmentRuleRequest, UpdateAssignmentRuleRequest
from app.services import assignment_service as svc
from app.services.auth_service import UserResponse, require_permission

# Assignee routing rules, category × outlet → user (Todo-Ops-Checklist P2).
router = APIRouter(prefix="/api/issue-assignment-rules", tags=["master-data"])

_VIEW = require_permission("master-data", "view")
_MANAGE = require_permission("master-data", "manage")


@router.get("", response_model=List[AssignmentRuleResponse])
def list_rules(db: Session = Depends(get_db), _: UserResponse = Depends(_VIEW)):
    return svc.list_rules(db)


@router.post("", response_model=AssignmentRuleResponse, status_code=201)
def create_rule(req: CreateAssignmentRuleRequest, db: Session = Depends(get_db),
                current_user: UserResponse = Depends(_MANAGE)):
    return svc.create_rule(db, req, actor=current_user.email)


@router.patch("/{rule_id}", response_model=AssignmentRuleResponse)
def update_rule(rule_id: str, req: UpdateAssignmentRuleRequest, db: Session = Depends(get_db),
                current_user: UserResponse = Depends(_MANAGE)):
    return svc.update_rule(db, svc.get_rule_or_404(db, rule_id), req, actor=current_user.email)


@router.delete("/{rule_id}", status_code=204)
def delete_rule(rule_id: str, db: Session = Depends(get_db), current_user: UserResponse = Depends(_MANAGE)):
    svc.delete_rule(db, svc.get_rule_or_404(db, rule_id), actor=current_user.email)
