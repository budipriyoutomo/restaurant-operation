"""Daily ops checklist KPIs — pure rules (Todo-Ops-Checklist Fase 3). No database.

Compliance = runs submitted on time ÷ runs that were due. A run is "due" once
it is finished (submitted / missed) or its window has closed; a run still open
today does not count against the outlet yet.
"""

from __future__ import annotations

from collections import Counter
from datetime import date, timedelta
from typing import Iterable, List, Optional


def compliance_rate(on_time: int, scheduled: int) -> Optional[float]:
    if scheduled <= 0:
        return None
    return round(on_time * 100 / scheduled, 1)


def counts_toward_compliance(status: str, closed: bool) -> bool:
    return status in ("submitted", "missed") or closed


def top_failing_items(titles: Iterable[str], n: int) -> List[dict]:
    """The items that failed most, as [{title, count}], ties by title."""
    counts = Counter(titles)
    ranked = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
    return [{"title": t, "count": c} for t, c in ranked[:n]]


def week_start(d: date) -> date:
    """Monday of d's ISO week."""
    return d - timedelta(days=d.isoweekday() - 1)
