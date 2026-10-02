"""Training enrollment & attendance (Todo-Pilot §9).

A TrainingProgram gets participants (TrainingEnrollment). Each enrollment
snapshots the participant's outlet and role name at enrollment time, so the
attendance recap does not shift when someone later changes outlet or role.

Pure functions (no DB):
  capacity_error, enrollment_outlet, attendance_error, validate_score,
  attendance_rate, attendance_recap
"""

from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, timezone
from typing import Iterable, List, Optional, Tuple

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.models.outlet import Outlet
from app.models.training_enrollment import TrainingEnrollment
from app.models.training_program import TrainingProgram
from app.models.user import User
from app.services.audit_service import write_audit

ENROLLMENT_STATUSES = ("registered", "attended", "no-show")
ALL_OUTLETS = "All Outlets"
MULTI_OUTLET = "Multi-outlet"


# ---------------------------------------------------------------------------
# Pure rules
# ---------------------------------------------------------------------------

def capacity_error(max_participants: Optional[int], current: int, adding: int) -> Optional[str]:
    """Why `adding` more people do not fit (None = they fit)."""
    if max_participants is None or current + adding <= max_participants:
        return None
    left = max(0, max_participants - current)
    if left == 0:
        return f"Training is full ({max_participants} participants)"
    return f"Only {left} of {max_participants} places left"


def enrollment_outlet(program_outlet: Optional[str], user_outlets: List[str]) -> str:
    """Outlet a participation is counted under: the program's outlet, else the
    participant's only outlet, else Multi-outlet."""
    if program_outlet and program_outlet != ALL_OUTLETS:
        return program_outlet
    if len(user_outlets) == 1:
        return user_outlets[0]
    return MULTI_OUTLET


def attendance_error(status: str, program_status: str, scheduled_date: Optional[date],
                     today: date) -> Optional[str]:
    """Why an enrollment cannot move to `status` now (None = it can)."""
    if status not in ENROLLMENT_STATUSES:
        return f"Unknown status {status!r}"
    if status == "registered":
        return None
    if program_status == "cancelled":
        return "The training was cancelled"
    if scheduled_date is not None and scheduled_date > today:
        return f"Attendance can be marked from {scheduled_date.isoformat()}"
    return None


def validate_score(status: str, score: Optional[int]) -> None:
    """A score (0–100) belongs to someone who attended."""
    if score is None:
        return
    if status != "attended":
        raise ValueError("Only an attended participant can have a score")
    if not 0 <= score <= 100:
        raise ValueError("Score must be between 0 and 100")


def attendance_rate(attended: int, no_show: int) -> Optional[float]:
    """% of marked participants who attended; None until anyone is marked."""
    marked = attended + no_show
    return round(attended * 100 / marked, 1) if marked else None


def _bucket(rows: List[str]) -> dict:
    attended = rows.count("attended")
    no_show = rows.count("no-show")
    return {
        "enrolled": len(rows),
        "attended": attended,
        "no_show": no_show,
        "pending": rows.count("registered"),
        "attendance_rate": attendance_rate(attended, no_show),
    }


def attendance_recap(rows: Iterable[Tuple[str, str, str]]) -> dict:
    """(outlet, role, status) rows → totals + per-outlet + per-role buckets (sorted by key)."""
    rows = list(rows)
    by_outlet: dict = defaultdict(list)
    by_role: dict = defaultdict(list)
    for outlet, role, status in rows:
        by_outlet[outlet].append(status)
        by_role[role].append(status)
    return {
        "totals": _bucket([s for _, _, s in rows]),
        "by_outlet": [{"key": k, **_bucket(v)} for k, v in sorted(by_outlet.items())],
        "by_role": [{"key": k, **_bucket(v)} for k, v in sorted(by_role.items())],
    }


# ---------------------------------------------------------------------------
# DB
# ---------------------------------------------------------------------------

def _user_outlet_names(db: Session, user: User) -> List[str]:
    from app.services.outlet_scope_service import allowed_outlet_ids
    ids = allowed_outlet_ids(db, user)
    if ids is None:                      # all outlets
        return []
    return [o.name for o in db.query(Outlet).filter(Outlet.id.in_(list(ids))).all()]


def enroll(db: Session, program: TrainingProgram, user_ids: list, actor: str) -> List[TrainingEnrollment]:
    """Enroll several people at once — all or nothing. The program row is locked
    so two managers filling the last places at the same time cannot overbook."""
    if program.status in ("cancelled", "completed"):
        raise HTTPException(status_code=409, detail=f"Cannot enroll into a {program.status} training")
    db.query(TrainingProgram).filter(TrainingProgram.id == program.id).with_for_update().one()

    unique_ids = list(dict.fromkeys(user_ids))   # UUIDs, validated by the request schema
    users = db.query(User).filter(User.id.in_(unique_ids), User.is_active.is_(True)).all()
    if len(users) != len(unique_ids):
        raise HTTPException(status_code=422, detail="Unknown or inactive user in the list")

    existing = {str(e.user_id) for e in program.enrollments}
    dupes = [u.name for u in users if str(u.id) in existing]
    if dupes:
        raise HTTPException(status_code=409, detail=f"Already enrolled: {', '.join(dupes)}")
    err = capacity_error(program.max_participants, len(program.enrollments), len(users))
    if err:
        raise HTTPException(status_code=409, detail=err)

    rows = []
    for u in users:
        e = TrainingEnrollment(
            program_id=program.id, user_id=u.id, user_name=u.name,
            role_name=u.role_obj.name if u.role_obj else u.role,
            outlet=enrollment_outlet(program.outlet, _user_outlet_names(db, u)),
        )
        db.add(e)
        rows.append(e)
    write_audit(db, table_name="training_programs", record_id=str(program.id), action="enroll",
                new_value={"title": program.title, "users": [u.name for u in users]}, performed_by=actor)
    db.commit()
    for e in rows:
        db.refresh(e)
    return rows


def mark(db: Session, program: TrainingProgram, e: TrainingEnrollment, status: Optional[str],
         score: Optional[int], notes: Optional[str], score_sent: bool, actor: str) -> TrainingEnrollment:
    new_status = status or e.status
    if status is not None and status != e.status:
        err = attendance_error(status, program.status, program.scheduled_date, date.today())
        if err:
            raise HTTPException(status_code=409, detail=err)
    new_score = score if score_sent else (e.score if new_status == "attended" else None)
    try:
        validate_score(new_status, new_score)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))

    old = {"status": e.status, "score": e.score}
    if new_status != e.status:
        e.marked_at = datetime.now(timezone.utc) if new_status != "registered" else None
    e.status, e.score = new_status, new_score
    if notes is not None:
        e.notes = notes
    write_audit(db, table_name="training_enrollments", record_id=str(e.id), action="attendance",
                old_value=old, new_value={"status": e.status, "score": e.score, "program": program.title,
                                          "user": e.user_name}, performed_by=actor)
    db.commit()
    db.refresh(e)
    return e
