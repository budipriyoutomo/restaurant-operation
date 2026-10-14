"""Paged lists (Todo-Pilot §22 / UX-9).

A list endpoint takes `limit` / `offset` and reports the full size in the
`X-Total-Count` header, so a phone on outlet Wi-Fi fetches one page at a time.
Without `limit` the whole list comes back, as before.
"""

from typing import Optional

from fastapi import Query, Response
from sqlalchemy.orm import Query as SAQuery

TOTAL_HEADER = "X-Total-Count"
MAX_LIMIT = 500


class Page:
    """FastAPI dependency: `?limit=&offset=`."""

    def __init__(
        self,
        limit: Optional[int] = Query(None, ge=1, le=MAX_LIMIT, description="Page size; omit for the whole list"),
        offset: int = Query(0, ge=0),
    ):
        self.limit = limit
        self.offset = offset


def paginate(query: SAQuery, page: Page, response: Response, *order_by, default_limit: Optional[int] = None) -> list:
    """Count, order and slice `query`; sets X-Total-Count on `response`.

    `order_by` must end in a unique column so pages never overlap.
    """
    response.headers[TOTAL_HEADER] = str(query.order_by(None).count())
    q = query.order_by(*order_by)
    if page.offset:
        q = q.offset(page.offset)
    limit = page.limit if page.limit is not None else default_limit
    if limit is not None:
        q = q.limit(limit)
    return q.all()


def like_pattern(text: str) -> str:
    """`%text%` for ILIKE, with the user's % and _ taken literally."""
    escaped = text.strip().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"
