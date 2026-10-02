"""Unit tests — training enrollment rules (Todo-Pilot §9). Pure, no database.

Written before the implementation (TDD).
"""

from datetime import date

import pytest

from app.services.training_service import (
    attendance_error,
    attendance_rate,
    attendance_recap,
    capacity_error,
    enrollment_outlet,
    validate_score,
)


class TestCapacity:
    def test_unlimited_when_no_max(self):
        assert capacity_error(None, 50, 10) is None

    def test_fits(self):
        assert capacity_error(10, 7, 3) is None

    def test_over_capacity_says_how_many_are_left(self):
        assert capacity_error(10, 8, 3) == "Only 2 of 10 places left"

    def test_full(self):
        assert capacity_error(5, 5, 1) == "Training is full (5 participants)"


class TestEnrollmentOutlet:
    def test_program_outlet_wins(self):
        assert enrollment_outlet("Jakarta", ["Bandung", "Dago"]) == "Jakarta"

    def test_users_single_outlet(self):
        assert enrollment_outlet(None, ["Bandung"]) == "Bandung"

    def test_multi_or_unknown(self):
        assert enrollment_outlet(None, ["Bandung", "Dago"]) == "Multi-outlet"
        assert enrollment_outlet(None, []) == "Multi-outlet"

    def test_all_outlets_sentinel_is_not_an_outlet(self):
        assert enrollment_outlet("All Outlets", ["Dago"]) == "Dago"


class TestAttendance:
    TODAY = date(2026, 10, 3)

    def test_registered_is_always_allowed(self):
        assert attendance_error("registered", "scheduled", date(2026, 12, 1), self.TODAY) is None

    def test_mark_on_or_after_the_day(self):
        assert attendance_error("attended", "scheduled", self.TODAY, self.TODAY) is None
        assert attendance_error("no-show", "completed", date(2026, 9, 1), self.TODAY) is None

    def test_cannot_mark_before_the_day(self):
        assert attendance_error("attended", "scheduled", date(2026, 10, 4), self.TODAY) == \
            "Attendance can be marked from 2026-10-04"

    def test_undated_program_can_be_marked(self):
        assert attendance_error("attended", "ongoing", None, self.TODAY) is None

    def test_cancelled_program(self):
        assert attendance_error("attended", "cancelled", None, self.TODAY) == "The training was cancelled"

    def test_unknown_status(self):
        assert attendance_error("present", "scheduled", None, self.TODAY) == "Unknown status 'present'"


class TestScore:
    def test_score_only_for_attended(self):
        validate_score("attended", 85)
        validate_score("attended", None)
        validate_score("no-show", None)
        with pytest.raises(ValueError):
            validate_score("no-show", 70)

    @pytest.mark.parametrize("score", [-1, 101])
    def test_range(self, score):
        with pytest.raises(ValueError):
            validate_score("attended", score)


class TestRecap:
    def test_rate_counts_only_marked(self):
        assert attendance_rate(attended=3, no_show=1) == 75.0
        assert attendance_rate(attended=0, no_show=0) is None

    def test_recap_by_outlet_and_role(self):
        rows = [
            ("Jakarta", "Staff", "attended"),
            ("Jakarta", "Staff", "no-show"),
            ("Jakarta", "Manager", "attended"),
            ("Bandung", "Staff", "registered"),
        ]
        r = attendance_recap(rows)
        assert r["totals"] == {"enrolled": 4, "attended": 2, "no_show": 1, "pending": 1, "attendance_rate": 66.7}
        assert r["by_outlet"] == [
            {"key": "Bandung", "enrolled": 1, "attended": 0, "no_show": 0, "pending": 1, "attendance_rate": None},
            {"key": "Jakarta", "enrolled": 3, "attended": 2, "no_show": 1, "pending": 0, "attendance_rate": 66.7},
        ]
        assert [x["key"] for x in r["by_role"]] == ["Manager", "Staff"]
        staff = next(x for x in r["by_role"] if x["key"] == "Staff")
        assert (staff["enrolled"], staff["attendance_rate"]) == (3, 50.0)

    def test_empty(self):
        r = attendance_recap([])
        assert r["totals"]["enrolled"] == 0 and r["by_outlet"] == [] and r["by_role"] == []
