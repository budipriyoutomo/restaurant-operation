"""Unit tests — PM schedule next-due-date arithmetic.

Purely in-memory: no database. These tests pin the contract that
pm_schedule_service.compute_next_due_date must satisfy (Todo-CMMS.md §2.1).
"""

from datetime import date

import pytest

from app.services.pm_schedule_service import compute_next_due_date


class TestDays:
    def test_add_30_days(self):
        assert compute_next_due_date(date(2026, 1, 1), "days", 30) == date(2026, 1, 31)

    def test_add_1_day(self):
        assert compute_next_due_date(date(2026, 2, 28), "days", 1) == date(2026, 3, 1)


class TestWeeks:
    def test_add_2_weeks(self):
        assert compute_next_due_date(date(2026, 1, 1), "weeks", 2) == date(2026, 1, 15)


class TestMonths:
    def test_add_1_month(self):
        assert compute_next_due_date(date(2026, 1, 15), "months", 1) == date(2026, 2, 15)

    def test_clamp_end_of_month(self):
        # Jan 31 + 1 month → Feb 28 (2026 is not a leap year)
        assert compute_next_due_date(date(2026, 1, 31), "months", 1) == date(2026, 2, 28)

    def test_leap_year_feb(self):
        assert compute_next_due_date(date(2028, 1, 31), "months", 1) == date(2028, 2, 29)

    def test_add_across_year_boundary(self):
        assert compute_next_due_date(date(2026, 11, 15), "months", 3) == date(2027, 2, 15)

    def test_add_12_months(self):
        assert compute_next_due_date(date(2026, 6, 30), "months", 12) == date(2027, 6, 30)


class TestValidation:
    def test_unknown_interval_type_raises(self):
        with pytest.raises(ValueError):
            compute_next_due_date(date(2026, 1, 1), "years", 1)

    def test_zero_interval_raises(self):
        with pytest.raises(ValueError):
            compute_next_due_date(date(2026, 1, 1), "days", 0)
