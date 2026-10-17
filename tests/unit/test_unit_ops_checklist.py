"""Pure rules of the daily ops checklist (Todo-Ops-Checklist Fase 1 §1.3).
Written before the implementation (TDD). No database.
"""

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from typing import Optional

import pytest

from app.services.ops_checklist_rules import (
    classify_submission, compute_run_window, dedup_key, evaluate_item, fail_issue_priority, is_due_on,
    local_date, recorded_at_problem, repeat_priority, run_score, submit_problems,
)

UTC = timezone.utc


def utc(y, mo, d, h, mi=0):
    return datetime(y, mo, d, h, mi, tzinfo=UTC)


# ── Run window ───────────────────────────────────────────────────────────────

class TestComputeRunWindow:
    def test_wib_opening_checklist(self):
        # 09:30 WIB = 02:30 UTC; opens 60 min before, closes 120 min after.
        opens, due, closes = compute_run_window(date(2026, 10, 16), time(9, 30), "Asia/Jakarta", 60, 120)
        assert (opens, due, closes) == (utc(2026, 10, 16, 1, 30), utc(2026, 10, 16, 2, 30), utc(2026, 10, 16, 4, 30))

    def test_wita_is_one_hour_ahead(self):
        _, due, _ = compute_run_window(date(2026, 10, 16), time(9, 30), "Asia/Makassar", 60, 120)
        assert due == utc(2026, 10, 16, 1, 30)

    def test_malaysia(self):
        _, due, _ = compute_run_window(date(2026, 10, 16), time(9, 30), "Asia/Kuala_Lumpur", 60, 120)
        assert due == utc(2026, 10, 16, 1, 30)

    def test_closing_checklist_past_midnight_is_due_the_next_calendar_day(self):
        # Shift date 16 Oct, due 00:30 → 17 Oct 00:30 WIB = 16 Oct 17:30 UTC.
        opens, due, closes = compute_run_window(date(2026, 10, 16), time(0, 30), "Asia/Jakarta", 60, 120)
        assert due == utc(2026, 10, 16, 17, 30)
        assert opens == utc(2026, 10, 16, 16, 30)          # 23:30 WIB on the shift date
        assert closes == utc(2026, 10, 16, 19, 30)

    def test_late_evening_stays_on_the_shift_date(self):
        _, due, _ = compute_run_window(date(2026, 10, 16), time(22, 30), "Asia/Jakarta", 60, 120)
        assert due == utc(2026, 10, 16, 15, 30)

    def test_cutoff_is_configurable(self):
        _, due, _ = compute_run_window(date(2026, 10, 16), time(5, 30), "Asia/Jakarta", 0, 0, cutoff=time(6, 0))
        assert due == utc(2026, 10, 16, 22, 30)            # 17 Oct 05:30 WIB

    def test_unknown_timezone_raises(self):
        with pytest.raises(ValueError):
            compute_run_window(date(2026, 10, 16), time(9, 30), "Mars/Olympus", 60, 120)


class TestLocalDate:
    def test_wita_is_already_tomorrow_while_wib_is_not(self):
        now = utc(2026, 10, 16, 16, 30)                    # 23:30 WIB, 00:30 WITA
        assert local_date(now, "Asia/Jakarta") == date(2026, 10, 16)
        assert local_date(now, "Asia/Makassar") == date(2026, 10, 17)


class TestIsDueOn:
    def test_empty_means_every_day(self):
        assert is_due_on([], date(2026, 10, 17)) is True

    def test_weekdays_only(self):
        weekdays = [1, 2, 3, 4, 5]
        assert is_due_on(weekdays, date(2026, 10, 16)) is True     # Friday
        assert is_due_on(weekdays, date(2026, 10, 17)) is False    # Saturday


# ── Items ────────────────────────────────────────────────────────────────────

class TestEvaluateItem:
    def test_inclusive_bounds(self):
        assert evaluate_item("temperature", -22, -22, -15) == "pass"
        assert evaluate_item("temperature", -15, -22, -15) == "pass"
        assert evaluate_item("temperature", -14.9, -22, -15) == "fail"
        assert evaluate_item("temperature", -22.1, -22, -15) == "fail"

    def test_open_bounds(self):
        assert evaluate_item("number", 1000, 10, None) == "pass"
        assert evaluate_item("number", -5, None, 4) == "pass"
        assert evaluate_item("number", 5, None, 4) == "fail"
        assert evaluate_item("number", 5, None, None) == "pass"

    def test_no_value_is_none(self):
        assert evaluate_item("temperature", None, 0, 4) is None

    def test_check_items_are_not_evaluated(self):
        with pytest.raises(ValueError):
            evaluate_item("check", 1, None, None)


@dataclass
class Item:
    title: str = "Item"
    item_type: str = "check"
    result: Optional[str] = "pass"
    requires_photo: bool = False
    photo_count: int = 0
    corrective_action: Optional[str] = None
    is_critical: bool = False


class TestSubmitProblems:
    def test_complete_run_has_no_problems(self):
        assert submit_problems([Item(), Item(result="na"), Item(result="fail")]) == []

    def test_unanswered(self):
        assert submit_problems([Item(title="Lantai", result=None)]) == ["Lantai: belum dijawab"]

    def test_required_photo(self):
        assert submit_problems([Item(title="Toilet", requires_photo=True)]) == ["Toilet: foto wajib"]
        assert submit_problems([Item(requires_photo=True, photo_count=1)]) == []
        assert submit_problems([Item(requires_photo=True, result="na")]) == []

    def test_failed_temperature_needs_a_corrective_action(self):
        t = Item(title="Freezer", item_type="temperature", result="fail")
        assert submit_problems([t]) == ["Freezer: tindakan koreksi wajib"]
        t.corrective_action = "   "
        assert submit_problems([t]) == ["Freezer: tindakan koreksi wajib"]
        t.corrective_action = "Produk dipindah ke freezer 2"
        assert submit_problems([t]) == []

    def test_failed_check_does_not_need_one(self):
        assert submit_problems([Item(result="fail")]) == []


class TestRunScore:
    def test_percent_of_pass_ignoring_na(self):
        assert run_score([Item(), Item(), Item(result="fail"), Item(result="na")]) == 66.7

    def test_none_when_nothing_applicable(self):
        assert run_score([Item(result="na")]) is None
        assert run_score([]) is None


# ── Submission timing ────────────────────────────────────────────────────────

class TestClassifySubmission:
    opens, due, closes = utc(2026, 10, 16, 1, 30), utc(2026, 10, 16, 2, 30), utc(2026, 10, 16, 4, 30)

    def test_before_open_is_rejected(self):
        assert classify_submission(self.opens - timedelta(seconds=1), self.opens, self.due, self.closes) == "rejected"

    def test_on_time_up_to_due(self):
        assert classify_submission(self.opens, self.opens, self.due, self.closes) == "on_time"
        assert classify_submission(self.due, self.opens, self.due, self.closes) == "on_time"

    def test_late_until_close(self):
        assert classify_submission(self.due + timedelta(seconds=1), self.opens, self.due, self.closes) == "late"
        assert classify_submission(self.closes, self.opens, self.due, self.closes) == "late"

    def test_after_close_is_rejected(self):
        assert classify_submission(self.closes + timedelta(seconds=1), self.opens, self.due, self.closes) == "rejected"


class TestRecordedAt:
    now = utc(2026, 10, 16, 2, 0)
    opens, closes = utc(2026, 10, 16, 1, 30), utc(2026, 10, 16, 4, 30)

    def test_inside_the_window_is_fine(self):
        assert recorded_at_problem(self.now - timedelta(minutes=10), self.now, self.opens, self.closes) is None

    def test_small_clock_skew_is_tolerated(self):
        assert recorded_at_problem(self.now + timedelta(minutes=4), self.now, self.opens, self.closes) is None

    def test_future_is_refused(self):
        assert recorded_at_problem(self.now + timedelta(minutes=6), self.now, self.opens, self.closes) is not None

    def test_outside_the_window_is_refused(self):
        assert recorded_at_problem(self.opens - timedelta(minutes=1), self.now, self.opens, self.closes) is not None
        late_now = utc(2026, 10, 16, 6, 0)
        assert recorded_at_problem(self.closes + timedelta(minutes=1), late_now, self.opens, self.closes) is not None


# ── Issues ───────────────────────────────────────────────────────────────────

class TestPriorities:
    def test_fail_issue_priority(self):
        assert fail_issue_priority(is_critical=True, is_repeat=False) == "critical"
        assert fail_issue_priority(is_critical=False, is_repeat=True) == "high"
        assert fail_issue_priority(is_critical=False, is_repeat=False) == "medium"

    def test_repeat_raises_to_high_and_never_lowers(self):
        assert repeat_priority("low") == "high"
        assert repeat_priority("medium") == "high"
        assert repeat_priority("high") is None
        assert repeat_priority("critical") is None


class TestDedupKey:
    def test_by_template_item(self):
        assert dedup_key("o1", "t1") == ("item", "o1", "t1")

    def test_by_asset_when_bound_to_one(self):
        assert dedup_key("o1", "t1", asset_id="a1") == ("asset", "o1", "a1")
