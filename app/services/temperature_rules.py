"""Food safety temperature rules — pure (Todo-Ops-Checklist Fase 2). No database.

A reading outside the asset's limits is an excursion. One excursion → a Food
Safety issue (with a corrective action). TEMP_CONSECUTIVE_LIMIT excursions in a
row, or one "major" excursion (≥ TEMP_MAJOR_DELTA °C beyond a limit), means
the equipment itself is failing → Maintenance issue + corrective work order.
"""

from __future__ import annotations

from typing import Optional, Sequence, Tuple

DEFAULT_MAJOR_DELTA = 3.0


def in_range(value, min_value, max_value) -> bool:
    """Inclusive bounds; None = unbounded."""
    v = float(value)
    if min_value is not None and v < float(min_value):
        return False
    if max_value is not None and v > float(max_value):
        return False
    return True


def excursion_severity(value, min_value, max_value, major_delta: float = DEFAULT_MAJOR_DELTA) -> Optional[str]:
    """None when in range; else minor | major by how far past the nearest limit."""
    if in_range(value, min_value, max_value):
        return None
    v = float(value)
    beyond = (float(min_value) - v) if (min_value is not None and v < float(min_value)) else (v - float(max_value))
    return "major" if beyond >= major_delta - 1e-9 else "minor"


def consecutive_excursions(in_range_newest_first: Sequence[bool], n: int) -> bool:
    """True when the n newest readings are all out of range."""
    if n <= 0 or len(in_range_newest_first) < n:
        return False
    return not any(in_range_newest_first[:n])


def effective_limits(item_min, item_max, asset_min, asset_max) -> Tuple:
    """Limits snapshotted into a run item: the item's own bound when set, else the asset's."""
    return (item_min if item_min is not None else asset_min,
            item_max if item_max is not None else asset_max)


def sensor_should_escalate(readings_newest_first: Sequence[Tuple], last_escalated_at, sustain, cooldown) -> bool:
    """Sensors report every few minutes; an open door is not an incident.

    readings_newest_first: (recorded_at, in_range) pairs. Escalate when the
    newest reading is out of range and the unbroken out-of-range streak it ends
    has lasted `sustain`. While the same streak goes on, escalate again only
    after `cooldown` since the last escalation; a streak that started after the
    last escalation is a new episode.
    """
    if not readings_newest_first or readings_newest_first[0][1]:
        return False
    newest = readings_newest_first[0][0]
    start = newest
    for at, ok in readings_newest_first:
        if ok:
            break
        start = at
    if newest - start < sustain:
        return False
    if last_escalated_at is None or last_escalated_at < start:
        return True
    return newest - last_escalated_at >= cooldown
