"""Unit tests — approval staleness helper (Tier 3)."""

from datetime import datetime, timedelta, timezone

from app.services.approval_service import is_step_stale


NOW = datetime(2026, 7, 6, 12, 0, tzinfo=timezone.utc)


class TestIsStepStale:
    def test_none_since_not_stale(self):
        assert is_step_stale(None, NOW, 3) is False

    def test_fresh_not_stale(self):
        assert is_step_stale(NOW - timedelta(days=1), NOW, 3) is False

    def test_exactly_threshold_is_stale(self):
        assert is_step_stale(NOW - timedelta(days=3), NOW, 3) is True

    def test_past_threshold_is_stale(self):
        assert is_step_stale(NOW - timedelta(days=10), NOW, 3) is True

    def test_zero_threshold_always_stale(self):
        assert is_step_stale(NOW, NOW, 0) is True

    def test_naive_datetime_treated_as_utc(self):
        naive = (NOW - timedelta(days=5)).replace(tzinfo=None)
        assert is_step_stale(naive, NOW, 3) is True
