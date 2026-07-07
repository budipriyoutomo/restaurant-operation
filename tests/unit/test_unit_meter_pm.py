"""Unit tests — meter-based PM due test (Tier 3)."""

from app.services.pm_schedule_service import meter_due


class TestMeterDue:
    def test_first_interval_due(self):
        # never generated, interval 500, latest 500 → due
        assert meter_due(500, None, 500) is True

    def test_below_first_interval_not_due(self):
        assert meter_due(499, None, 500) is False

    def test_next_interval_due(self):
        # last generated at 500, interval 500 → threshold 1000
        assert meter_due(1000, 500, 500) is True
        assert meter_due(999, 500, 500) is False

    def test_no_reading_not_due(self):
        assert meter_due(None, 0, 500) is False

    def test_zero_interval_not_due(self):
        assert meter_due(1000, 0, 0) is False
