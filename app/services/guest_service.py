"""Guest Service — guest complaints with recovery and response KPIs (Todo-Pilot §8).

A guest complaint is an Issue (category "Guest Service"), so it keeps the
whole cascade: Task, notifications, closure rules. Each such Issue has exactly
one GuestCase with what the Issue lacks: who the guest is, the channel the
complaint came through, when it was reported, when staff first responded,
when it was resolved, and the recovery given (compensation type + IDR value).

KPIs:
  first response = first_response_at - reported_at
  resolution     = resolved_at - reported_at, where resolved_at is the first
                   time the Issue reached resolved/closed (a reopen clears it)

Pure functions (no DB):
  minutes_between, next_resolved_at, validate_compensation, kpi_summary
"""

from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from statistics import median
from typing import Iterable, List, Optional

from fastapi import HTTPException
from sqlalchemy import event, inspect
from sqlalchemy.orm import Session

from app.config import settings
from app.models.guest_case import GuestCase
from app.models.issue import Issue
from app.schemas.guest_case import GuestCaseResponse, GuestKpiResponse
from app.services.audit_service import write_audit

CHANNELS = ("walk-in", "phone", "google-review", "instagram", "whatsapp", "other")
COMPENSATION_TYPES = ("none", "discount", "free-item", "voucher", "refund", "other")
# Compensation that is money by nature must carry its value.
_VALUE_REQUIRED = {"discount", "voucher", "refund"}

_RESOLVED = {"resolved", "closed"}


# ---------------------------------------------------------------------------
# Pure rules
# ---------------------------------------------------------------------------

def minutes_between(start: Optional[datetime], end: Optional[datetime]) -> Optional[int]:
    """Whole minutes from start to end; None if either is missing; never negative."""
    if start is None or end is None:
        return None
    return max(0, int((end - start).total_seconds() // 60))


def next_resolved_at(old_status: str, new_status: str, current: Optional[datetime],
                     now: datetime) -> Optional[datetime]:
    """resolved_at after an Issue status change: stamped on the first move to
    resolved/closed, kept through resolved → closed, cleared by a reopen.
    A cancellation is not a resolution."""
    if new_status in _RESOLVED:
        return current or now
    return None


def validate_compensation(ctype: str, value: int) -> None:
    """Raise ValueError when a recovery entry does not make sense."""
    if ctype not in COMPENSATION_TYPES:
        raise ValueError(f"Unknown compensation type {ctype!r}")
    if value < 0:
        raise ValueError("Compensation value cannot be negative")
    if ctype == "none" and value != 0:
        raise ValueError("No compensation means a value of 0")
    if ctype in _VALUE_REQUIRED and value == 0:
        raise ValueError(f"A {ctype} needs its value in IDR")


def _avg(xs: List[int]) -> Optional[int]:
    return round(sum(xs) / len(xs)) if xs else None


def _median(xs: List[int]) -> Optional[int]:
    return round(median(xs)) if xs else None


def kpi_summary(cases: Iterable, target_minutes: int) -> dict:
    """Aggregate case views (first_response_minutes, resolution_minutes, channel,
    outlet, compensation_value, currency, is_open) into the KPI payload."""
    cases = list(cases)
    responses = [c.first_response_minutes for c in cases if c.first_response_minutes is not None]
    resolutions = [c.resolution_minutes for c in cases if c.resolution_minutes is not None]

    compensation: dict = defaultdict(int)
    for c in cases:
        if c.compensation_value:
            compensation[c.currency] += c.compensation_value

    by_outlet: dict = defaultdict(list)
    for c in cases:
        by_outlet[c.outlet].append(c)

    return {
        "cases": len(cases),
        "open": sum(1 for c in cases if c.is_open),
        "responded": len(responses),
        "resolved": len(resolutions),
        "avgFirstResponseMinutes": _avg(responses),
        "medianFirstResponseMinutes": _median(responses),
        "avgResolutionMinutes": _avg(resolutions),
        "medianResolutionMinutes": _median(resolutions),
        "targetMinutes": target_minutes,
        "withinTargetPct": (round(sum(1 for r in responses if r <= target_minutes) * 100 / len(responses), 1)
                            if responses else None),
        "byChannel": dict(Counter(c.channel for c in cases)),
        "compensationTotal": dict(compensation),
        "perOutlet": [
            {
                "outlet": outlet,
                "cases": len(rows),
                "open": sum(1 for c in rows if c.is_open),
                "avgFirstResponseMinutes": _avg([c.first_response_minutes for c in rows
                                                 if c.first_response_minutes is not None]),
                "avgResolutionMinutes": _avg([c.resolution_minutes for c in rows
                                              if c.resolution_minutes is not None]),
            }
            for outlet, rows in sorted(by_outlet.items())
        ],
    }


# ---------------------------------------------------------------------------
# DB
# ---------------------------------------------------------------------------

GUEST_CATEGORY = "Guest Service"
_OPEN_EXCLUDED = {"resolved", "closed", "cancelled"}


def _v(x) -> str:
    return x.value if hasattr(x, "value") else str(x)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def parse_reported_at(raw: Optional[str]) -> datetime:
    """ISO datetime from the UI → aware datetime. Default now; the future is a 422."""
    if not raw:
        return _now()
    try:
        dt = datetime.fromisoformat(raw)
    except ValueError:
        raise HTTPException(status_code=422, detail="reportedAt must be an ISO datetime")
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    if dt > _now() + timedelta(minutes=5):
        raise HTTPException(status_code=422, detail="reportedAt cannot be in the future")
    return dt


def ensure_case(db: Session, issue: Issue) -> Optional[GuestCase]:
    """Give a Guest Service issue its case (called by issue_service.create_issue)."""
    if _v(issue.category) != GUEST_CATEGORY:
        return None
    case = GuestCase(issue_id=issue.id, outlet=issue.outlet, outlet_id=issue.outlet_id, reported_at=_now())
    db.add(case)
    return case


def case_to_response(c: GuestCase) -> GuestCaseResponse:
    i = c.issue
    return GuestCaseResponse(
        id=str(c.id), issueId=str(c.issue_id), issueNumber=i.number, title=i.title,
        status=_v(i.status), priority=_v(i.priority), outlet=c.outlet,
        guestName=c.guest_name or "", guestContact=c.guest_contact or "", channel=c.channel,
        reportedAt=c.reported_at.isoformat(),
        firstResponseAt=c.first_response_at.isoformat() if c.first_response_at else None,
        firstResponseBy=c.first_response_by or "", firstResponseNote=c.first_response_note or "",
        resolvedAt=c.resolved_at.isoformat() if c.resolved_at else None,
        firstResponseMinutes=minutes_between(c.reported_at, c.first_response_at),
        resolutionMinutes=minutes_between(c.reported_at, c.resolved_at),
        compensationType=c.compensation_type, compensationValue=int(c.compensation_value or 0),
        currency=c.currency, compensationNote=c.compensation_note or "",
    )


def respond(db: Session, c: GuestCase, note: str, actor_name: str) -> GuestCaseResponse:
    """Record the first response. Later calls keep the first one (the KPI is *first* response)."""
    if c.first_response_at is None:
        c.first_response_at = _now()
        c.first_response_by = actor_name
        c.first_response_note = note
        write_audit(db, table_name="guest_cases", record_id=str(c.id), action="respond",
                    new_value={"issue": c.issue.number, "note": note}, performed_by=actor_name)
        db.commit()
        db.refresh(c)
    return case_to_response(c)


def record_recovery(db: Session, c: GuestCase, req, actor: str) -> GuestCaseResponse:
    try:
        validate_compensation(req.compensationType, req.compensationValue)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    old = {"compensationType": c.compensation_type, "compensationValue": int(c.compensation_value or 0)}
    c.compensation_type = req.compensationType
    c.compensation_value = req.compensationValue
    c.currency = req.currency.upper()
    c.compensation_note = req.note
    write_audit(db, table_name="guest_cases", record_id=str(c.id), action="recovery", old_value=old,
                new_value={"issue": c.issue.number, "compensationType": c.compensation_type,
                           "compensationValue": c.compensation_value, "currency": c.currency},
                performed_by=actor)
    db.commit()
    db.refresh(c)
    return case_to_response(c)


class _KpiView:
    def __init__(self, c: GuestCase):
        self.first_response_minutes = minutes_between(c.reported_at, c.first_response_at)
        self.resolution_minutes = minutes_between(c.reported_at, c.resolved_at)
        self.channel, self.outlet = c.channel, c.outlet
        self.compensation_value, self.currency = int(c.compensation_value or 0), c.currency
        self.is_open = _v(c.issue.status) not in _OPEN_EXCLUDED


def kpis(cases: List[GuestCase]) -> GuestKpiResponse:
    # A cancelled complaint (e.g. logged by mistake) says nothing about service.
    counted = [c for c in cases if _v(c.issue.status) != "cancelled"]
    return GuestKpiResponse(**kpi_summary([_KpiView(c) for c in counted],
                                          target_minutes=settings.GUEST_FIRST_RESPONSE_TARGET_MINUTES))


@event.listens_for(Session, "before_flush")
def _track_resolution(session: Session, flush_context, instances) -> None:
    """Keep guest_cases.resolved_at in step with the Issue status, wherever the
    status was changed (PATCH, task roll-up, reopen, cancel, approval …)."""
    with session.no_autoflush:
        for obj in list(session.dirty):
            if not isinstance(obj, Issue):
                continue
            hist = inspect(obj).attrs.status.history
            if not hist.has_changes():
                continue
            case = obj.guest_case
            if case is None:
                continue
            old = _v(hist.deleted[0]) if hist.deleted else ""
            now = _now()
            case.resolved_at = next_resolved_at(old, _v(obj.status), case.resolved_at, now)
            if case.resolved_at is not None and case.first_response_at is None:
                # Resolving the complaint is itself a response to the guest.
                case.first_response_at = case.resolved_at
