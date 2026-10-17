"""Manager's daily sign-off — pure rules (Todo-Ops-Checklist Fase 4). No database.

A manager signs "today's logs at this outlet are checked" once per outlet per
business day. A business day runs from the 05:00 cutoff to the next 05:00 at
the outlet, the same day a closing checklist due 00:30 belongs to.

What was signed is fixed by a digest (SHA-256) over the day's runs and
temperature readings. A reading added afterwards changes the digest, so an
inspector can see that the data is no longer what the manager signed.
"""

from __future__ import annotations

import hashlib
import json
from datetime import date, datetime, timedelta, timezone
from typing import Iterable, Optional, Sequence, Tuple

from app.services.ops_checklist_rules import BUSINESS_DAY_CUTOFF, zone

_OPEN = ("pending", "in_progress")


def business_day_bounds(day: date, tz: str) -> Tuple[datetime, datetime]:
    """[start, end) of one business day at the outlet, in UTC."""
    start = datetime.combine(day, BUSINESS_DAY_CUTOFF, tzinfo=zone(tz))
    end = datetime.combine(day + timedelta(days=1), BUSINESS_DAY_CUTOFF, tzinfo=zone(tz))
    return start.astimezone(timezone.utc), end.astimezone(timezone.utc)


def signoff_problem(day: date, outlet_today: date, runs: Sequence, now: datetime) -> Optional[str]:
    """Why the day cannot be signed yet, or None. Runs expose status and closes_at.
    A run still open past its window can no longer be filled in: it counts as closed."""
    if day > outlet_today:
        return "Hari ini belum berjalan — tidak bisa ditandatangani"
    if not runs:
        return "Tidak ada checklist pada hari itu"
    running = [r for r in runs if r.status in _OPEN and r.closes_at > now]
    if running:
        return f"{len(running)} checklist masih berjalan — tunggu sampai jendelanya tutup"
    return None


def day_summary(runs: Iterable[dict], logs: Iterable[dict]) -> dict:
    runs, logs = list(runs), list(logs)
    return {
        "runs": len(runs),
        "submitted": sum(1 for r in runs if r["status"] == "submitted"),
        "late": sum(1 for r in runs if r["status"] == "submitted" and r.get("isLate")),
        "missed": sum(1 for r in runs if r["status"] == "missed"),
        "failedItems": sum(1 for r in runs for i in r.get("items", []) if i.get("result") == "fail"),
        "temperatureReadings": len(logs),
        "excursions": sum(1 for log in logs if not log["inRange"]),
    }


def _canonical(runs: Iterable[dict], logs: Iterable[dict]) -> str:
    runs = sorted(({**r, "items": sorted(r.get("items", []), key=lambda i: json.dumps(i, sort_keys=True, default=str))}
                   for r in runs), key=lambda r: r["number"])
    logs = sorted(logs, key=lambda log: json.dumps(log, sort_keys=True, default=str))
    return json.dumps({"runs": runs, "logs": logs}, sort_keys=True, default=str, separators=(",", ":"))


def day_digest(runs: Iterable[dict], logs: Iterable[dict]) -> str:
    """SHA-256 hex of the day's data — independent of row order."""
    return hashlib.sha256(_canonical(runs, logs).encode("utf-8")).hexdigest()
