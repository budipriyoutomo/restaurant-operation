"""Pure KPI rules of the daily ops checklist (Todo-Ops-Checklist Fase 3). TDD."""

from datetime import date

from app.services.ops_kpi_rules import compliance_rate, counts_toward_compliance, top_failing_items, week_start


class TestComplianceRate:
    def test_on_time_over_scheduled(self):
        assert compliance_rate(on_time=7, scheduled=10) == 70.0
        assert compliance_rate(on_time=2, scheduled=3) == 66.7

    def test_nothing_scheduled_is_none(self):
        assert compliance_rate(on_time=0, scheduled=0) is None


class TestCountsTowardCompliance:
    def test_finished_runs_count(self):
        assert counts_toward_compliance("submitted", closed=False) is True
        assert counts_toward_compliance("missed", closed=True) is True

    def test_open_runs_count_only_once_their_window_closed(self):
        assert counts_toward_compliance("pending", closed=False) is False
        assert counts_toward_compliance("in_progress", closed=True) is True


class TestTopFailingItems:
    def test_most_failures_first_then_title(self):
        rows = ["Sabun", "Lantai", "Sabun", "Freezer", "Lantai", "Sabun"]
        assert top_failing_items(rows, 2) == [{"title": "Sabun", "count": 3}, {"title": "Lantai", "count": 2}]

    def test_ties_by_title(self):
        assert top_failing_items(["b", "a"], 5) == [{"title": "a", "count": 1}, {"title": "b", "count": 1}]

    def test_empty(self):
        assert top_failing_items([], 3) == []


class TestWeekStart:
    def test_monday(self):
        assert week_start(date(2026, 10, 16)) == date(2026, 10, 12)    # Friday
        assert week_start(date(2026, 10, 12)) == date(2026, 10, 12)
        assert week_start(date(2026, 10, 18)) == date(2026, 10, 12)    # Sunday
