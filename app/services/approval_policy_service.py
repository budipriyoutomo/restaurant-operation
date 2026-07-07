"""Approval Policy engine (Todo-CMMS.md §2.2).

Pure function (no DB):
  resolve_policy_steps  — pick the best-matching policy's step chain

DB helpers:
  active_policies_for_type  — load candidate policies
  resolve_steps_for_request — DB → pure resolver → step defs (or None)
  policy_to_response        — ORM → ApprovalPolicyResponse

"Smart default + override": if no policy matches, the caller falls back to the
hardcoded default 2-step chain (manager → admin). This mirrors the touchedToggles
pattern used elsewhere — a sensible default that an explicit policy can override.
"""

from __future__ import annotations

from typing import List, Optional

from sqlalchemy.orm import Session

from app.models.approval_policy import ApprovalPolicy
from app.schemas.approval_policy import ApprovalPolicyResponse, PolicyStep


# ---------------------------------------------------------------------------
# Pure resolver
# ---------------------------------------------------------------------------

def _amount_matches(policy_min: Optional[int], policy_max: Optional[int], amount: int) -> bool:
    if policy_min is not None and amount < policy_min:
        return False
    if policy_max is not None and amount > policy_max:
        return False
    return True


def resolve_policy_steps(
    policies: list,
    approval_type: str,
    amount: Optional[int],
    outlet: Optional[str] = None,
) -> Optional[List[dict]]:
    """Return the best-matching policy's steps as a sorted list of
    {"order": int, "role": str}, or None if nothing matches.

    `policies` is any iterable of objects/dicts exposing: is_active, approval_type,
    min_amount, max_amount, outlet, steps (and optionally created_at). Only active
    policies of the right type whose amount range and outlet match are considered.

    Tie-break (most specific first):
      1. outlet-specific policy beats an all-outlets policy
      2. higher min_amount (kicks in at a higher threshold) wins
      3. narrower max_amount wins
      4. newest created_at wins
    """
    amount_eff = amount if amount is not None else 0

    def get(p, attr):
        return p.get(attr) if isinstance(p, dict) else getattr(p, attr, None)

    matches = []
    for p in policies:
        if not get(p, "is_active"):
            continue
        p_type = get(p, "approval_type")
        p_type = p_type.value if hasattr(p_type, "value") else p_type
        if p_type != approval_type:
            continue
        p_outlet = get(p, "outlet")
        if p_outlet is not None and p_outlet != outlet:
            continue
        if not _amount_matches(get(p, "min_amount"), get(p, "max_amount"), amount_eff):
            continue
        matches.append(p)

    if not matches:
        return None

    NEG_INF = float("-inf")
    POS_INF = float("inf")

    def sort_key(p):
        p_outlet = get(p, "outlet")
        outlet_specific = 1 if (p_outlet is not None and p_outlet == outlet) else 0
        min_amt = get(p, "min_amount")
        max_amt = get(p, "max_amount")
        created = get(p, "created_at")
        created_ord = created.timestamp() if hasattr(created, "timestamp") else 0
        return (
            outlet_specific,
            min_amt if min_amt is not None else NEG_INF,
            -(max_amt if max_amt is not None else POS_INF),
            created_ord,
        )

    best = sorted(matches, key=sort_key, reverse=True)[0]
    raw_steps = get(best, "steps") or []
    ordered = sorted(raw_steps, key=lambda s: int(s["order"]))
    # Renumber 1..n contiguous so step_order aligns with current_step_order,
    # which the decision logic advances by exactly 1 each approval.
    steps = [{"order": i + 1, "role": str(s["role"])} for i, s in enumerate(ordered)]
    return steps or None


# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------

def active_policies_for_type(db: Session, approval_type: str) -> List[ApprovalPolicy]:
    return (
        db.query(ApprovalPolicy)
        .filter(
            ApprovalPolicy.approval_type == approval_type,
            ApprovalPolicy.is_active.is_(True),
            ApprovalPolicy.deleted_at.is_(None),
        )
        .all()
    )


def resolve_steps_for_request(
    db: Session,
    approval_type: str,
    amount: Optional[int],
    outlet: Optional[str] = None,
) -> Optional[List[dict]]:
    """Load candidate policies and resolve steps, or None if no policy matches."""
    policies = active_policies_for_type(db, approval_type)
    return resolve_policy_steps(policies, approval_type, amount, outlet)


def policy_to_response(p: ApprovalPolicy) -> ApprovalPolicyResponse:
    approval_type = p.approval_type.value if hasattr(p.approval_type, "value") else str(p.approval_type)
    return ApprovalPolicyResponse(
        id=str(p.id),
        approvalType=approval_type,
        minAmount=p.min_amount,
        maxAmount=p.max_amount,
        steps=[PolicyStep(order=int(s["order"]), role=str(s["role"])) for s in (p.steps or [])],
        outlet=p.outlet,
        isActive=bool(p.is_active),
        createdAt=p.created_at.isoformat() if p.created_at else "",
    )
