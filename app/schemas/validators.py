"""Shared request-field validators."""

from typing import Optional


def in_enum(value: Optional[str], enum_cls) -> Optional[str]:
    """Reject a value outside `enum_cls` as a 422 naming the field, instead of a
    Postgres enum error surfacing as a 500. Returns the plain str unchanged."""
    allowed = [e.value for e in enum_cls]
    if value is not None and value not in allowed:
        raise ValueError(f"must be one of {allowed}")
    return value
