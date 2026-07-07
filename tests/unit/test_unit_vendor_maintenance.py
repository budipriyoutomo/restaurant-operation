"""Unit tests — vendor maintenance pure helper (Tier 3)."""

from app.services.vendor_maintenance_service import on_time_pct


class TestOnTimePct:
    def test_none_completed_is_zero(self):
        assert on_time_pct(0, 0) == 0.0

    def test_all_on_time(self):
        assert on_time_pct(4, 4) == 100.0

    def test_partial(self):
        assert on_time_pct(3, 4) == 75.0
