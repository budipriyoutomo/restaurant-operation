"""Deep links to a record in the frontend (Todo-Pilot §14 / UX-1).

Email and WhatsApp link straight to the record a notification is about, so the
recipient lands on the approval / work order / issue instead of the home page.
Paths must match the frontend routes (frontend/lib/routes.ts).
"""

from typing import Optional

from app.config import settings

# notifications.entity_type → frontend path segment
ENTITY_PATHS = {
    "issues":      "issues",
    "approvals":   "approvals",
    "work_orders": "work-orders",
}


def record_url(entity_type: Optional[str], entity_id=None) -> Optional[str]:
    """Pure: absolute URL to the record, the app home when the record has no
    route, or None when APP_URL is not configured."""
    base = (settings.APP_URL or "").rstrip("/")
    if not base:
        return None
    path = ENTITY_PATHS.get(entity_type or "")
    if not path or entity_id is None:
        return base
    return f"{base}/{path}/{entity_id}"
