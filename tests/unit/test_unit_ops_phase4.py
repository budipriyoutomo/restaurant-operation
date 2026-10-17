"""Pure rules of Todo-Ops-Checklist Fase 4. Written before the implementation (TDD).

  - weekly / monthly schedules (deep cleaning, thermometer calibration)
  - the manager's daily sign-off (business day, digest of what was signed)
  - temperature sensors (sustained excursion, re-escalation cooldown)
  - PDF text (core fonts are latin-1)
  - built-in templates per outlet type
"""

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone

import pytest

from app.services.ops_checklist_rules import (
    FREQUENCIES, candidate_run_dates, is_scheduled_on, schedule_problem,
)
from app.services.ops_signoff_rules import business_day_bounds, day_digest, day_summary, signoff_problem
from app.services.temperature_rules import sensor_should_escalate
from app.services.ops_report_rules import pdf_text
from app.services.ops_template_presets import PRESETS, preset_problems

UTC = timezone.utc


def utc(y, mo, d, h, mi=0):
    return datetime(y, mo, d, h, mi, tzinfo=UTC)


# ── Weekly / monthly schedules ───────────────────────────────────────────────

class TestIsScheduledOn:
    def test_daily_keeps_weekday_filter(self):
        assert is_scheduled_on("daily", [], None, date(2026, 10, 17)) is True
        assert is_scheduled_on("daily", [1, 2, 3, 4, 5], None, date(2026, 10, 17)) is False   # Saturday

    def test_weekly_on_its_day(self):
        assert is_scheduled_on("weekly", [7], None, date(2026, 10, 18)) is True     # Sunday
        assert is_scheduled_on("weekly", [7], None, date(2026, 10, 17)) is False

    def test_monthly_on_its_day(self):
        assert is_scheduled_on("monthly", [], 15, date(2026, 10, 15)) is True
        assert is_scheduled_on("monthly", [], 15, date(2026, 10, 16)) is False

    def test_monthly_day_past_month_end_falls_on_last_day(self):
        assert is_scheduled_on("monthly", [], 31, date(2026, 2, 28)) is True
        assert is_scheduled_on("monthly", [], 31, date(2026, 2, 27)) is False
        assert is_scheduled_on("monthly", [], 30, date(2028, 2, 29)) is True     # leap year
        assert is_scheduled_on("monthly", [], 31, date(2026, 10, 30)) is False   # October has a 31st

    def test_unknown_frequency(self):
        with pytest.raises(ValueError):
            is_scheduled_on("yearly", [], None, date(2026, 10, 16))


class TestScheduleProblem:
    def test_frequencies(self):
        assert FREQUENCIES == ("daily", "weekly", "monthly")

    def test_daily_ok(self):
        assert schedule_problem("daily", [], None, 60, 120) is None

    def test_daily_window_at_most_a_day(self):
        assert schedule_problem("daily", [], None, 720, 720) is None
        assert schedule_problem("daily", [], None, 1000, 500) is not None

    def test_daily_takes_no_day_of_month(self):
        assert schedule_problem("daily", [], 5, 60, 120) is not None

    def test_weekly_needs_exactly_one_day(self):
        assert schedule_problem("weekly", [7], None, 60, 120) is None
        assert schedule_problem("weekly", [], None, 60, 120) is not None
        assert schedule_problem("weekly", [6, 7], None, 60, 120) is not None

    def test_weekly_window_shorter_than_a_week(self):
        # Opens 6 days before, 1 day grace: exactly one week — the next occurrence never overlaps.
        assert schedule_problem("weekly", [7], None, 6 * 1440, 1440) is None
        assert schedule_problem("weekly", [7], None, 6 * 1440, 1441) is not None

    def test_monthly_needs_day_of_month(self):
        assert schedule_problem("monthly", [], 1, 60, 120) is None
        assert schedule_problem("monthly", [], None, 60, 120) is not None
        assert schedule_problem("monthly", [], 32, 60, 120) is not None
        assert schedule_problem("monthly", [], 0, 60, 120) is not None

    def test_monthly_window_within_shortest_month(self):
        assert schedule_problem("monthly", [], 1, 27 * 1440, 1440) is None
        assert schedule_problem("monthly", [], 1, 28 * 1440, 1) is not None

    def test_negative_minutes(self):
        assert schedule_problem("daily", [], None, -1, 120) is not None


class TestCandidateRunDates:
    def test_no_window_only_today(self):
        assert candidate_run_dates(date(2026, 10, 16), 0) == [date(2026, 10, 16)]

    def test_short_window_checks_tomorrow_too(self):
        # Extra candidates are harmless: the generator creates a future run only once it opens.
        assert candidate_run_dates(date(2026, 10, 16), 60) == [date(2026, 10, 16), date(2026, 10, 17)]

    def test_long_window_looks_ahead(self):
        # A run opening 3 days before its date must exist from then on.
        assert candidate_run_dates(date(2026, 10, 16), 3 * 1440) == [date(2026, 10, 16) + timedelta(days=i) for i in range(4)]

    def test_partial_day_rounds_up(self):
        assert candidate_run_dates(date(2026, 10, 16), 1441) == [date(2026, 10, 16), date(2026, 10, 17), date(2026, 10, 18)]


# ── Daily sign-off ───────────────────────────────────────────────────────────

@dataclass
class RunView:
    status: str
    closes_at: datetime


class TestBusinessDayBounds:
    def test_wib_from_cutoff_to_cutoff(self):
        # 16 Oct 05:00 WIB → 17 Oct 05:00 WIB, in UTC
        assert business_day_bounds(date(2026, 10, 16), "Asia/Jakarta") == (utc(2026, 10, 15, 22), utc(2026, 10, 16, 22))


class TestSignoffProblem:
    now = utc(2026, 10, 16, 16)          # 23:00 WIB

    def test_ok_when_every_run_is_closed(self):
        runs = [RunView("submitted", utc(2026, 10, 16, 4)), RunView("missed", utc(2026, 10, 16, 10))]
        assert signoff_problem(date(2026, 10, 16), date(2026, 10, 16), runs, self.now) is None

    def test_future_day(self):
        assert signoff_problem(date(2026, 10, 17), date(2026, 10, 16), [], self.now) is not None

    def test_run_still_open(self):
        runs = [RunView("pending", utc(2026, 10, 16, 17))]
        assert "1" in signoff_problem(date(2026, 10, 16), date(2026, 10, 16), runs, self.now)

    def test_open_run_past_its_window_is_as_good_as_missed(self):
        # The 15-minute job has not marked it yet — it can no longer be filled in.
        runs = [RunView("in_progress", utc(2026, 10, 16, 15))]
        assert signoff_problem(date(2026, 10, 16), date(2026, 10, 16), runs, self.now) is None

    def test_nothing_to_sign(self):
        assert signoff_problem(date(2026, 10, 16), date(2026, 10, 16), [], self.now) is not None


def _run(number="OPS-1", status="submitted", late=False, score=100.0, items=()):
    return {"number": number, "status": status, "isLate": late, "score": score,
            "submittedAt": "2026-10-16T02:00:00+00:00", "items": list(items)}


def _item(title="Sabun", result="pass", value=None, notes="", corrective=None):
    return {"title": title, "result": result, "value": value, "notes": notes, "correctiveAction": corrective}


def _log(asset="a1", at="2026-10-16T03:00:00+00:00", value=-18.0, ok=True, corrective=None, source="checklist"):
    return {"assetId": asset, "recordedAt": at, "value": value, "inRange": ok, "correctiveAction": corrective,
            "source": source}


class TestDaySummary:
    def test_counts(self):
        runs = [_run(items=[_item(), _item("Lantai", "fail")]), _run("OPS-2", late=True), _run("OPS-3", "missed", score=None)]
        logs = [_log(), _log(value=-10.0, ok=False, corrective="pindah")]
        assert day_summary(runs, logs) == {
            "runs": 3, "submitted": 2, "late": 1, "missed": 1, "failedItems": 1,
            "temperatureReadings": 2, "excursions": 1,
        }


class TestDayDigest:
    def test_stable_and_order_insensitive(self):
        a, b = _run("OPS-1", items=[_item()]), _run("OPS-2")
        logs = [_log(), _log("a2")]
        assert day_digest([a, b], logs) == day_digest([b, a], list(reversed(logs)))
        assert len(day_digest([a], [])) == 64

    def test_any_change_changes_it(self):
        base = day_digest([_run(items=[_item()])], [_log()])
        assert day_digest([_run(items=[_item(notes="x")])], [_log()]) != base
        assert day_digest([_run(items=[_item()])], [_log(value=-17.9)]) != base
        assert day_digest([_run(items=[_item()])], [_log(), _log("a2")]) != base


# ── Temperature sensors ──────────────────────────────────────────────────────

SUSTAIN, COOLDOWN = timedelta(minutes=15), timedelta(minutes=60)
T0 = utc(2026, 10, 16, 3)


def readings(*pairs):
    """(minutes after T0, in_range) oldest first → newest first."""
    return [(T0 + timedelta(minutes=m), ok) for m, ok in reversed(pairs)]


class TestSensorShouldEscalate:
    def test_in_range_never(self):
        assert sensor_should_escalate(readings((0, False), (20, True)), None, SUSTAIN, COOLDOWN) is False

    def test_short_spike_door_open(self):
        assert sensor_should_escalate(readings((0, True), (5, False), (10, False)), None, SUSTAIN, COOLDOWN) is False

    def test_sustained(self):
        assert sensor_should_escalate(readings((0, True), (5, False), (20, False)), None, SUSTAIN, COOLDOWN) is True

    def test_cooldown_after_escalation(self):
        r = readings((0, False), (15, False), (30, False))
        assert sensor_should_escalate(r, T0 + timedelta(minutes=15), SUSTAIN, COOLDOWN) is False
        r = readings((0, False), (15, False), (80, False))
        assert sensor_should_escalate(r, T0 + timedelta(minutes=15), SUSTAIN, COOLDOWN) is True

    def test_new_episode_ignores_old_escalation(self):
        # Escalated at 0–15, back in range at 20, out again from 25 → a new episode at 40.
        r = readings((0, False), (15, False), (20, True), (25, False), (40, False))
        assert sensor_should_escalate(r, T0 + timedelta(minutes=15), SUSTAIN, COOLDOWN) is True

    def test_empty(self):
        assert sensor_should_escalate([], None, SUSTAIN, COOLDOWN) is False


# ── PDF text ─────────────────────────────────────────────────────────────────

class TestPdfText:
    def test_keeps_latin1(self):
        assert pdf_text("Freezer −18 °C – café") == "Freezer -18 °C - café"

    def test_drops_what_core_fonts_cannot_draw(self):
        assert pdf_text("Bersih 👍 ✓") == "Bersih ? ?"
        assert pdf_text(None) == ""
        assert pdf_text("“kutip” ‘satu’ …") == '"kutip" \'satu\' ...'


# ── Built-in templates ───────────────────────────────────────────────────────

class TestPresets:
    def test_three_outlet_types(self):
        assert set(PRESETS) == {"cafe", "resto", "cloud_kitchen"}

    def test_every_preset_is_valid(self):
        for key, preset in PRESETS.items():
            assert preset_problems(preset) == [], key

    def test_each_has_opening_and_closing(self):
        for preset in PRESETS.values():
            shifts = {t["shift"] for t in preset["templates"]}
            assert {"opening", "closing"} <= shifts

    def test_problems_are_reported(self):
        bad = {"label": "x", "templates": [{"name": "", "shift": "noon", "dueTime": "25:00", "items": []}]}
        assert len(preset_problems(bad)) >= 3
