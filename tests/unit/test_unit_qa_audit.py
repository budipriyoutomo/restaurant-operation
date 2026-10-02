"""Unit tests — QA audit checklist rules (Todo-Pilot §7). Pure, no database.

Written before the implementation (TDD).
"""

from datetime import date
from types import SimpleNamespace as NS

import pytest

from app.services.qa_audit_service import (
    compute_score,
    finding_issue_priority,
    find_repeats,
    monthly_trend,
    submit_problems,
)


def F(result, weight=1, requires_photo=False, has_photo=False, title="Item"):
    return NS(result=result, weight=weight, requires_photo=requires_photo, has_photo=has_photo, title=title)


# ---------------------------------------------------------------------------
# Score
# ---------------------------------------------------------------------------

class TestScore:
    def test_all_pass_is_100(self):
        assert compute_score([F("pass"), F("pass")]) == 100.0

    def test_weighted(self):
        # pass weight 3, fail weight 1 → 3/4
        assert compute_score([F("pass", 3), F("fail", 1)]) == 75.0

    def test_na_is_excluded(self):
        assert compute_score([F("pass", 1), F("na", 5)]) == 100.0

    def test_rounded_to_one_decimal(self):
        assert compute_score([F("pass"), F("fail"), F("fail")]) == 33.3

    def test_nothing_applicable_has_no_score(self):
        assert compute_score([F("na"), F("na")]) is None
        assert compute_score([]) is None

    def test_unanswered_items_are_not_scored(self):
        assert compute_score([F("pass"), F(None)]) == 100.0


# ---------------------------------------------------------------------------
# Submit validation
# ---------------------------------------------------------------------------

class TestSubmitProblems:
    def test_complete_audit_has_no_problems(self):
        assert submit_problems([F("pass"), F("fail"), F("na")]) == []

    def test_unanswered_item_blocks(self):
        problems = submit_problems([F("pass"), F(None, title="Suhu chiller")])
        assert problems == ["Suhu chiller: not answered"]

    def test_required_photo_missing_blocks(self):
        problems = submit_problems([F("fail", requires_photo=True, title="Lantai dapur")])
        assert problems == ["Lantai dapur: photo required"]

    def test_required_photo_present_is_fine(self):
        assert submit_problems([F("pass", requires_photo=True, has_photo=True)]) == []

    def test_na_needs_no_photo(self):
        assert submit_problems([F("na", requires_photo=True)]) == []

    def test_all_problems_reported(self):
        problems = submit_problems([F(None, title="A"), F("fail", requires_photo=True, title="B")])
        assert problems == ["A: not answered", "B: photo required"]


# ---------------------------------------------------------------------------
# Issue priority for a failed finding
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("critical,repeat,expected", [
    (False, False, "medium"),
    (False, True, "high"),
    (True, False, "critical"),
    (True, True, "critical"),
])
def test_finding_issue_priority(critical, repeat, expected):
    assert finding_issue_priority(critical, repeat) == expected


# ---------------------------------------------------------------------------
# Repeat findings
# ---------------------------------------------------------------------------

class TestRepeats:
    def test_item_failed_last_time_is_repeat(self):
        assert find_repeats({"a", "b"}, {"b", "c"}) == {"b"}

    def test_no_previous_audit_means_no_repeats(self):
        assert find_repeats({"a"}, None) == set()

    def test_nothing_failed_now(self):
        assert find_repeats(set(), {"a"}) == set()


# ---------------------------------------------------------------------------
# Monthly trend per outlet
# ---------------------------------------------------------------------------

class TestMonthlyTrend:
    def test_average_per_outlet_per_month(self):
        rows = [
            ("Jakarta", date(2026, 9, 3), 80.0),
            ("Jakarta", date(2026, 9, 20), 90.0),
            ("Jakarta", date(2026, 10, 1), 70.0),
            ("Bandung", date(2026, 9, 5), 60.0),
        ]
        assert monthly_trend(rows) == {
            "Jakarta": [{"month": "2026-09", "score": 85.0, "audits": 2},
                        {"month": "2026-10", "score": 70.0, "audits": 1}],
            "Bandung": [{"month": "2026-09", "score": 60.0, "audits": 1}],
        }

    def test_audits_without_score_are_ignored(self):
        assert monthly_trend([("Jakarta", date(2026, 9, 1), None)]) == {}

    def test_months_are_sorted(self):
        rows = [("X", date(2026, 10, 1), 50.0), ("X", date(2026, 8, 1), 70.0)]
        assert [m["month"] for m in monthly_trend(rows)["X"]] == ["2026-08", "2026-10"]
