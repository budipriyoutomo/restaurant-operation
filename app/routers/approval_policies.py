from datetime import datetime, timezone
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.approval_policy import ApprovalPolicy
from app.models.enums import ApprovalTypeEnum, ApproverRoleEnum
from app.schemas.approval_policy import (
    ApprovalPolicyResponse,
    CreateApprovalPolicyRequest,
    PolicyStep,
    UpdateApprovalPolicyRequest,
)
from app.services import approval_policy_service as policy_svc
from app.services.audit_service import write_audit
from app.services.outlet_scope_service import resolve_outlet_id
from app.services.auth_service import UserResponse, get_current_user, require_roles

router = APIRouter(prefix="/api/approval-policies", tags=["approvals"])

_VALID_TYPES = {e.value for e in ApprovalTypeEnum}
_VALID_ROLES = {e.value for e in ApproverRoleEnum}


def _validate(approval_type: str, steps: List[PolicyStep],
              min_amount: Optional[int], max_amount: Optional[int]) -> None:
    if approval_type not in _VALID_TYPES:
        raise HTTPException(status_code=422, detail=f"approvalType must be one of {sorted(_VALID_TYPES)}")
    if not steps:
        raise HTTPException(status_code=422, detail="steps must not be empty")
    for s in steps:
        if s.role not in _VALID_ROLES:
            raise HTTPException(status_code=422, detail=f"step role must be one of {sorted(_VALID_ROLES)}")
    if min_amount is not None and max_amount is not None and min_amount > max_amount:
        raise HTTPException(status_code=422, detail="minAmount must be <= maxAmount")


def _steps_to_json(steps: List[PolicyStep]) -> list:
    return [{"order": s.order, "role": s.role} for s in steps]


# ── Endpoints ─────────────────────────────────────────────────────────────────

@router.get("", response_model=List[ApprovalPolicyResponse])
def list_policies(
    approval_type: Optional[str] = Query(None),
    active_only: bool = Query(False),
    db: Session = Depends(get_db),
    _: UserResponse = Depends(get_current_user),
):
    q = db.query(ApprovalPolicy).filter(ApprovalPolicy.deleted_at.is_(None))
    if approval_type:
        q = q.filter(ApprovalPolicy.approval_type == approval_type)
    if active_only:
        q = q.filter(ApprovalPolicy.is_active.is_(True))
    policies = q.order_by(ApprovalPolicy.approval_type, ApprovalPolicy.min_amount).all()
    return [policy_svc.policy_to_response(p) for p in policies]


@router.post("", response_model=ApprovalPolicyResponse, status_code=201)
def create_policy(
    req: CreateApprovalPolicyRequest,
    db: Session = Depends(get_db),
    _: UserResponse = Depends(require_roles("admin")),
):
    _validate(req.approvalType, req.steps, req.minAmount, req.maxAmount)
    policy = ApprovalPolicy(
        approval_type=req.approvalType,
        min_amount=req.minAmount,
        max_amount=req.maxAmount,
        steps=_steps_to_json(req.steps),
        outlet=req.outlet,
        outlet_id=resolve_outlet_id(db, req.outlet),
        is_active=req.isActive,
    )
    db.add(policy)
    db.flush()
    write_audit(db, table_name="approval_policies", record_id=str(policy.id), action="create",
                new_value={"approvalType": req.approvalType, "steps": _steps_to_json(req.steps)})
    db.commit()
    db.refresh(policy)
    return policy_svc.policy_to_response(policy)


@router.get("/{policy_id}", response_model=ApprovalPolicyResponse)
def get_policy(
    policy_id: str,
    db: Session = Depends(get_db),
    _: UserResponse = Depends(get_current_user),
):
    policy = (
        db.query(ApprovalPolicy)
        .filter(ApprovalPolicy.id == policy_id, ApprovalPolicy.deleted_at.is_(None))
        .first()
    )
    if not policy:
        raise HTTPException(status_code=404, detail="Approval policy not found")
    return policy_svc.policy_to_response(policy)


@router.patch("/{policy_id}", response_model=ApprovalPolicyResponse)
def update_policy(
    policy_id: str,
    req: UpdateApprovalPolicyRequest,
    db: Session = Depends(get_db),
    _: UserResponse = Depends(require_roles("admin")),
):
    policy = (
        db.query(ApprovalPolicy)
        .filter(ApprovalPolicy.id == policy_id, ApprovalPolicy.deleted_at.is_(None))
        .first()
    )
    if not policy:
        raise HTTPException(status_code=404, detail="Approval policy not found")

    new_type = req.approvalType if req.approvalType is not None else (
        policy.approval_type.value if hasattr(policy.approval_type, "value") else str(policy.approval_type)
    )
    new_steps = req.steps if req.steps is not None else [
        PolicyStep(order=int(s["order"]), role=str(s["role"])) for s in (policy.steps or [])
    ]
    new_min = req.minAmount if req.minAmount is not None else policy.min_amount
    new_max = req.maxAmount if req.maxAmount is not None else policy.max_amount
    _validate(new_type, new_steps, new_min, new_max)

    if req.approvalType is not None:
        policy.approval_type = req.approvalType
    if req.minAmount is not None:
        policy.min_amount = req.minAmount
    if req.maxAmount is not None:
        policy.max_amount = req.maxAmount
    if req.steps is not None:
        policy.steps = _steps_to_json(req.steps)
    if req.outlet is not None:
        policy.outlet = req.outlet
        # Matching is on outlet_id — keep it in step or the policy silently
        # matches the wrong outlet after a change.
        policy.outlet_id = resolve_outlet_id(db, req.outlet)
    if req.isActive is not None:
        policy.is_active = req.isActive

    policy.updated_at = datetime.now(timezone.utc)
    write_audit(db, table_name="approval_policies", record_id=str(policy.id), action="update",
                new_value={"approvalType": new_type})
    db.commit()
    db.refresh(policy)
    return policy_svc.policy_to_response(policy)


@router.delete("/{policy_id}", status_code=204)
def delete_policy(
    policy_id: str,
    db: Session = Depends(get_db),
    _: UserResponse = Depends(require_roles("admin")),
):
    policy = (
        db.query(ApprovalPolicy)
        .filter(ApprovalPolicy.id == policy_id, ApprovalPolicy.deleted_at.is_(None))
        .first()
    )
    if not policy:
        raise HTTPException(status_code=404, detail="Approval policy not found")
    policy.deleted_at = datetime.now(timezone.utc)
    policy.is_active = False
    write_audit(db, table_name="approval_policies", record_id=str(policy.id), action="delete",
                old_value={"approvalType": policy.approval_type.value if hasattr(policy.approval_type, "value") else str(policy.approval_type)})
    db.commit()
