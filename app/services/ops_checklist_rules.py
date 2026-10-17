"""Daily ops checklist — pure rules (Todo-Ops-Checklist Fase 1 §1.3). No database.

Time: a schedule's due_time is wall-clock time at the outlet (outlets.timezone).
A run belongs to a *shift date* (run_date). A due_time before the business-day
cutoff (05:00) belongs to the night of the shift date, i.e. the next calendar
day — a closing checklist due 00:30 for the 16th is due on the 17th at 00:30.
All instants are returned in UTC.

Window: opens_at = due_at − open_minutes_before, closes_at = due_at + grace.
Submitting before opens_at or after closes_at is refused; after due_at it is
late. A run still open at closes_at becomes missed and is locked — a food
safety log filled in afterwards proves nothing.
"""

from __future__ import annotations

import calendar
import math
from datetime import date, datetime, time, timedelta, timezone
from typing import Iterable, List, Optional, Sequence, Tuple
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

RESULTS = ("pass", "fail", "na")
ITEM_TYPES = ("check", "number", "temperature")
SHIFTS = ("opening", "midday", "closing", "other")
RUN_STATUSES = ("pending", "in_progress", "submitted", "missed")
FREQUENCIES = ("daily", "weekly", "monthly")
# open_minutes_before + grace_minutes may not exceed the gap between two
# occurrences, so the next run never opens while the last is still open.
# Monthly: the shortest month.
MAX_WINDOW_MINUTES = {"daily": 24 * 60, "weekly": 7 * 24 * 60, "monthly": 28 * 24 * 60}

BUSINESS_DAY_CUTOFF = time(5, 0)
# A device clock may run a little fast; beyond this a recorded_at is "in the future".
CLOCK_SKEW = timedelta(minutes=5)

_PRIORITY_RANK = {"low": 0, "medium": 1, "high": 2, "critical": 3}


def zone(tz: str) -> ZoneInfo:
    try:
        return ZoneInfo(tz)
    except (ZoneInfoNotFoundError, ValueError, TypeError):
        raise ValueError(f"Unknown timezone {tz!r}")


def compute_run_window(run_date: date, due_time: time, tz: str, open_minutes_before: int, grace_minutes: int,
                       cutoff: time = BUSINESS_DAY_CUTOFF) -> Tuple[datetime, datetime, datetime]:
    """(opens_at, due_at, closes_at) in UTC for one run."""
    due_day = run_date + timedelta(days=1) if due_time < cutoff else run_date
    due_local = datetime.combine(due_day, due_time, tzinfo=zone(tz))
    due = due_local.astimezone(timezone.utc)
    return due - timedelta(minutes=open_minutes_before), due, due + timedelta(minutes=grace_minutes)


def local_date(now_utc: datetime, tz: str) -> date:
    """Today at the outlet — the run_date the generator creates runs for."""
    return now_utc.astimezone(zone(tz)).date()


def is_due_on(days_of_week: Sequence[int], run_date: date) -> bool:
    """days_of_week: ISO weekdays 1 (Mon) … 7 (Sun); empty = every day."""
    return not days_of_week or run_date.isoweekday() in days_of_week


def is_scheduled_on(frequency: str, days_of_week: Sequence[int], day_of_month: Optional[int], run_date: date) -> bool:
    """daily: days_of_week filter (empty = every day). weekly: its one weekday.
    monthly: day_of_month, or the month's last day when the month is shorter."""
    if frequency in ("daily", "weekly"):
        return is_due_on(days_of_week, run_date)
    if frequency == "monthly":
        last = calendar.monthrange(run_date.year, run_date.month)[1]
        return day_of_month is not None and run_date.day == min(day_of_month, last)
    raise ValueError(f"Unknown frequency {frequency!r}")


def schedule_problem(frequency: str, days_of_week: Sequence[int], day_of_month: Optional[int],
                     open_minutes_before: int, grace_minutes: int) -> Optional[str]:
    """Why this schedule shape is invalid, or None."""
    if frequency not in FREQUENCIES:
        return f"frequency must be one of {', '.join(FREQUENCIES)}"
    if open_minutes_before < 0 or grace_minutes < 0:
        return "openMinutesBefore and graceMinutes cannot be negative"
    if frequency == "weekly" and len(days_of_week or []) != 1:
        return "A weekly schedule is due on exactly one weekday"
    if frequency == "monthly":
        if day_of_month is None or not 1 <= day_of_month <= 31:
            return "A monthly schedule needs dayOfMonth 1–31"
    elif day_of_month is not None:
        return "dayOfMonth is only for monthly schedules"
    limit = MAX_WINDOW_MINUTES[frequency]
    if open_minutes_before + grace_minutes > limit:
        return f"openMinutesBefore + graceMinutes cannot exceed {limit} minutes for a {frequency} schedule"
    return None


def candidate_run_dates(today: date, open_minutes_before: int) -> List[date]:
    """Shift dates whose run may already be open today: a run opening days
    before its date (weekly deep cleaning) must exist from then on."""
    ahead = math.ceil(max(open_minutes_before, 0) / 1440)
    return [today + timedelta(days=i) for i in range(ahead + 1)]


def evaluate_item(item_type: str, value, min_value, max_value) -> Optional[str]:
    """pass | fail for a number/temperature reading; bounds inclusive, None = unbounded."""
    if item_type not in ("number", "temperature"):
        raise ValueError(f"{item_type!r} items are answered pass/fail, not measured")
    if value is None:
        return None
    v = float(value)
    if min_value is not None and v < float(min_value):
        return "fail"
    if max_value is not None and v > float(max_value):
        return "fail"
    return "pass"


def submit_problems(items: Iterable) -> List[str]:
    """Why the run cannot be submitted yet (empty = it can). Items expose
    title, item_type, result, requires_photo, photo_count, corrective_action."""
    problems: List[str] = []
    for i in items:
        if i.result not in RESULTS:
            problems.append(f"{i.title}: belum dijawab")
            continue
        if i.requires_photo and i.result != "na" and not i.photo_count:
            problems.append(f"{i.title}: foto wajib")
        if i.item_type == "temperature" and i.result == "fail" and not (i.corrective_action or "").strip():
            problems.append(f"{i.title}: tindakan koreksi wajib")
    return problems


def run_score(items: Iterable) -> Optional[float]:
    """% of applicable items (pass/fail; na excluded) that passed. None when none apply."""
    applicable = [i for i in items if i.result in ("pass", "fail")]
    if not applicable:
        return None
    return round(sum(1 for i in applicable if i.result == "pass") * 100 / len(applicable), 1)


def classify_submission(now: datetime, opens_at: datetime, due_at: datetime, closes_at: datetime) -> str:
    """on_time | late | rejected (before opening or after closing)."""
    if now < opens_at or now > closes_at:
        return "rejected"
    return "on_time" if now <= due_at else "late"


def recorded_at_problem(recorded_at: datetime, now: datetime, opens_at: datetime, closes_at: datetime) -> Optional[str]:
    """Why a device timestamp is not acceptable evidence, or None."""
    if recorded_at > now + CLOCK_SKEW:
        return "recordedAt is in the future"
    if recorded_at < opens_at or recorded_at > closes_at:
        return "recordedAt is outside the checklist window"
    return None


def fail_issue_priority(is_critical: bool, is_repeat: bool) -> str:
    if is_critical:
        return "critical"
    return "high" if is_repeat else "medium"


def repeat_priority(current: str) -> Optional[str]:
    """The priority an open Issue goes to when its item fails again: high, never lower."""
    return "high" if _PRIORITY_RANK.get(current, 1) < _PRIORITY_RANK["high"] else None


def dedup_key(outlet_id, template_item_id, asset_id=None) -> tuple:
    """One open Issue per key (keputusan #8): per asset when the item measures an
    asset (Fase 2), otherwise per template item."""
    if asset_id is not None:
        return ("asset", outlet_id, asset_id)
    return ("item", outlet_id, template_item_id)
