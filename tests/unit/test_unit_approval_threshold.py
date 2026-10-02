"""Unit tests — per-outlet work-order approval threshold (Todo-Next §2.2).

Purely in-memory. An outlet's own threshold wins; NULL falls back to the global
default. A threshold of 0 is a real value ("every costed WO needs approval"),
not "unset".
"""

from app.services.work_order_service import needs_approval, resolve_approval_threshold


def test_outlet_threshold_wins():
    assert resolve_approval_threshold(5_000_000, default=1_000_000) == 5_000_000


def test_null_falls_back_to_default():
    assert resolve_approval_threshold(None, default=1_000_000) == 1_000_000


def test_zero_is_a_real_threshold():
    assert resolve_approval_threshold(0, default=1_000_000) == 0


def test_needs_approval_strictly_above_threshold():
    assert needs_approval(1_000_001, 1_000_000) is True
    assert needs_approval(1_000_000, 1_000_000) is False


def test_no_estimate_never_needs_approval():
    assert needs_approval(None, 0) is False
