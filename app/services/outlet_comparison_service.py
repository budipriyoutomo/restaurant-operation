"""GM multi-outlet view (Todo-Pilot §25 / UX-12).

One row per outlet the caller may see, on what a GM acts on: open issues and
SLA, QA score, guests waiting for a first response, this month's budget and
repair downtime. Each row carries `flags` (the exceptions) and rows with more
exceptions come first. The same numbers feed a daily/weekly digest sent to
users who opt in (preferences `digestDaily` / `digestWeekly`).
"""

from __future__ import annotations

import os
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, List, Optional
from zoneinfo import ZoneInfo

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models.asset import WorkOrder
from app.models.guest_case import GuestCase
from app.models.issue import Issue
from app.models.outlet import Outlet
from app.models.qa_audit import QAAuditSession
from app.models.user import User
from app.services.issue_closure_service import is_ready_to_close

# Exception thresholds — shown on screen next to the numbers.
QA_MIN_SCORE = 80.0           # average audit score below this is an exception
DOWNTIME_MAX_HOURS = 8.0      # repair downtime in the window above this
BUDGET_MAX_PCT = 100.0        # spend above the month's budget

ISSUE_OPEN_EXCLUDED = ("resolved", "closed", "cancelled")

DIGEST_HOUR = 7               # local time the digest goes out (hourly runner)
DIGEST_TZ = os.getenv("DIGEST_TZ", "Asia/Jakarta")


def _v(x) -> str:
    return x.value if hasattr(x, "value") else str(x)


def compare_outlets(db: Session, user=None, days: int = 30, today: Optional[date] = None) -> Dict[str, Any]:
    """`user=None` = every outlet of the current company (the digest job)."""
    from app.services.budget_service import compute_budget_status
    from app.services.outlet_scope_service import allowed_outlet_ids

    today = today or date.today()
    since = today - timedelta(days=days)
    since_ts = datetime.combine(since, datetime.min.time(), tzinfo=timezone.utc)

    q = db.query(Outlet).filter(Outlet.deleted_at.is_(None))
    allowed = allowed_outlet_ids(db, user) if user is not None else None
    if allowed is not None:
        q = q.filter(Outlet.id.in_(allowed or [None]))
    outlets = q.order_by(Outlet.name).all()
    ids = [o.id for o in outlets]

    budgets = {b["outletId"]: b["pct"] for b in compute_budget_status(db, today.strftime("%Y-%m"), user=user)}

    open_issues = (db.query(Issue).filter(Issue.outlet_id.in_(ids or [None]))
                   .filter(Issue.status.notin_(ISSUE_OPEN_EXCLUDED)).all())
    ready = [i for i in db.query(Issue).filter(Issue.outlet_id.in_(ids or [None]),
                                               Issue.status.notin_(("closed", "cancelled"))).all()
             if is_ready_to_close(i)]

    qa = dict(db.query(QAAuditSession.outlet_id, func.avg(QAAuditSession.score))
              .filter(QAAuditSession.outlet_id.in_(ids or [None]), QAAuditSession.status == "submitted",
                      QAAuditSession.score.isnot(None), QAAuditSession.audit_date >= since)
              .group_by(QAAuditSession.outlet_id).all())

    guests_waiting = dict(db.query(GuestCase.outlet_id, func.count(GuestCase.id))
                          .filter(GuestCase.outlet_id.in_(ids or [None]), GuestCase.first_response_at.is_(None),
                                  GuestCase.resolved_at.is_(None))
                          .group_by(GuestCase.outlet_id).all())
    response = {}
    for oid, rep, first in (db.query(GuestCase.outlet_id, GuestCase.reported_at, GuestCase.first_response_at)
                            .filter(GuestCase.outlet_id.in_(ids or [None]), GuestCase.first_response_at.isnot(None),
                                    GuestCase.reported_at >= since_ts).all()):
        response.setdefault(oid, []).append((first - rep).total_seconds() / 60)

    downtime: Dict[Any, float] = {}
    for oid, start, end in (db.query(WorkOrder.outlet_id, WorkOrder.downtime_start, WorkOrder.downtime_end)
                            .filter(WorkOrder.outlet_id.in_(ids or [None]), WorkOrder.downtime_start.isnot(None),
                                    WorkOrder.downtime_end.isnot(None), WorkOrder.downtime_end >= since_ts).all()):
        downtime[oid] = downtime.get(oid, 0.0) + max((end - start).total_seconds() / 3600, 0.0)

    rows = []
    for o in outlets:
        mine = [i for i in open_issues if i.outlet_id == o.id]
        late = sum(1 for i in mine if i.due_date is not None and i.due_date < today)
        qa_score = round(float(qa[o.id]), 1) if o.id in qa and qa[o.id] is not None else None
        waiting = int(guests_waiting.get(o.id, 0))
        times = response.get(o.id)
        budget_pct = budgets.get(str(o.id))
        hours = round(downtime.get(o.id, 0.0), 1)
        flags = []
        if late:
            flags.append("sla")
        if qa_score is not None and qa_score < QA_MIN_SCORE:
            flags.append("qa")
        if waiting:
            flags.append("guest")
        if budget_pct is not None and budget_pct > BUDGET_MAX_PCT:
            flags.append("budget")
        if hours > DOWNTIME_MAX_HOURS:
            flags.append("downtime")
        rows.append({
            "outletId": str(o.id), "outlet": o.name,
            "openIssues": len(mine), "slaBreached": late,
            "readyToClose": sum(1 for i in ready if i.outlet_id == o.id),
            "qaScore": qa_score,
            "guestWaiting": waiting,
            "guestResponseMinutes": round(sum(times) / len(times)) if times else None,
            "budgetPct": budget_pct,
            "downtimeHours": hours,
            "flags": flags,
        })
    rows.sort(key=lambda r: (-len(r["flags"]), -r["slaBreached"], -r["openIssues"], r["outlet"]))
    return {
        "since": since.isoformat(), "days": days, "outlets": rows,
        "thresholds": {"qaMinScore": QA_MIN_SCORE, "downtimeMaxHours": DOWNTIME_MAX_HOURS, "budgetMaxPct": BUDGET_MAX_PCT},
    }


# ---------------------------------------------------------------------------
# Digest
# ---------------------------------------------------------------------------

_FLAG_TEXT = {
    "sla": ("{n} lewat SLA", "{n} past SLA"),
    "qa": ("skor QA {n}", "QA score {n}"),
    "guest": ("{n} tamu menunggu", "{n} guests waiting"),
    "budget": ("anggaran {n}%", "budget {n}%"),
    "downtime": ("downtime {n} jam", "downtime {n} h"),
}
_FLAG_VALUE = {"sla": "slaBreached", "qa": "qaScore", "guest": "guestWaiting", "budget": "budgetPct", "downtime": "downtimeHours"}


def digest_text(data: Dict[str, Any], period: str):
    from app.services.notification_texts import L
    rows = data["outlets"]
    total_open = sum(r["openIssues"] for r in rows)
    total_late = sum(r["slaBreached"] for r in rows)
    lines_id, lines_en = [], []
    for r in rows:
        if not r["flags"]:
            continue
        parts_id = [_FLAG_TEXT[f][0].format(n=r[_FLAG_VALUE[f]]) for f in r["flags"]]
        parts_en = [_FLAG_TEXT[f][1].format(n=r[_FLAG_VALUE[f]]) for f in r["flags"]]
        lines_id.append(f"• {r['outlet']}: " + ", ".join(parts_id))
        lines_en.append(f"• {r['outlet']}: " + ", ".join(parts_en))
    head_id = f"{total_open} laporan terbuka, {total_late} lewat SLA di {len(rows)} outlet."
    head_en = f"{total_open} open issues, {total_late} past SLA across {len(rows)} outlets."
    body_id = "\n".join([head_id] + (lines_id or ["Tidak ada pengecualian. 👍"]))
    body_en = "\n".join([head_en] + (lines_en or ["No exceptions. 👍"]))
    title = L("Ringkasan harian outlet", "Daily outlet summary") if period == "daily" \
        else L("Ringkasan mingguan outlet", "Weekly outlet summary")
    return title, L(body_id, body_en)


def _due_periods(prefs: dict, local: datetime) -> List[str]:
    if local.hour < DIGEST_HOUR:
        return []
    out = []
    if prefs.get("digestDaily") and prefs.get("digestDailySent") != local.date().isoformat():
        out.append("daily")
    if prefs.get("digestWeekly") and local.weekday() == 0 and prefs.get("digestWeeklySent") != local.date().isoformat():
        out.append("weekly")
    return out


def send_digests(db: Session, now: Optional[datetime] = None) -> List[Dict[str, Any]]:
    """Send due digests in the current company. Safe to run hourly: each
    user gets at most one daily (and one Monday weekly) digest per day."""
    from app.services.auth_service import _user_to_response, has_permission
    from app.services.notification_service import _create

    now = now or datetime.now(timezone.utc)
    local = now.astimezone(ZoneInfo(DIGEST_TZ)) if now.tzinfo else now
    sent: List[Dict[str, Any]] = []
    for user in db.query(User).filter(User.is_active.is_(True)).all():
        prefs = dict(user.preferences or {})
        periods = _due_periods(prefs, local)
        if not periods:
            continue
        resp = _user_to_response(user)
        if not has_permission(resp, "dashboard"):
            continue
        for period in periods:
            data = compare_outlets(db, resp, days=1 if period == "daily" else 7, today=local.date())
            title, message = digest_text(data, period)
            _create(db, user, title, message, "info", event="outlet_digest")
            prefs[f"digest{period.capitalize()}Sent"] = local.date().isoformat()
            sent.append({"user": user.email, "period": period})
        user.preferences = prefs
    db.commit()
    return sent
