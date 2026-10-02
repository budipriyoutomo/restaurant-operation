"""Unit tests — Guest Service rules (Todo-Pilot §8). Pure, no database.

Written before the implementation (TDD).
"""

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace as NS

import pytest

from app.services.guest_service import (
    CHANNELS,
    COMPENSATION_TYPES,
    kpi_summary,
    minutes_between,
    next_resolved_at,
    validate_compensation,
)

T0 = datetime(2026, 10, 1, 12, 0, tzinfo=timezone.utc)


def at(minutes):
    return T0 + timedelta(minutes=minutes)


class TestMinutesBetween:
    def test_whole_minutes(self):
        assert minutes_between(T0, at(45)) == 45

    def test_missing_end_is_none(self):
        assert minutes_between(T0, None) is None
        assert minutes_between(None, T0) is None

    def test_never_negative(self):
        # a response logged with a clock slightly behind the report time
        assert minutes_between(at(10), T0) == 0


class TestResolvedAt:
    def test_first_resolution_stamps_now(self):
        assert next_resolved_at("in-progress", "resolved", None, T0) == T0

    def test_closing_after_resolve_keeps_first_stamp(self):
        assert next_resolved_at("resolved", "closed", at(-60), T0) == at(-60)

    def test_closing_directly_stamps(self):
        assert next_resolved_at("open", "closed", None, T0) == T0

    def test_reopen_clears(self):
        assert next_resolved_at("resolved", "in-progress", at(-60), T0) is None

    def test_cancel_is_not_a_resolution(self):
        assert next_resolved_at("open", "cancelled", None, T0) is None

    def test_other_moves_keep_value(self):
        assert next_resolved_at("open", "assigned", None, T0) is None


class TestCompensation:
    def test_types_and_channels(self):
        assert CHANNELS == ("walk-in", "phone", "google-review", "instagram", "whatsapp", "other")
        assert COMPENSATION_TYPES == ("none", "discount", "free-item", "voucher", "refund", "other")

    def test_none_must_have_zero_value(self):
        validate_compensation("none", 0)
        with pytest.raises(ValueError):
            validate_compensation("none", 50_000)

    @pytest.mark.parametrize("ctype", ["discount", "voucher", "refund"])
    def test_money_types_need_a_value(self, ctype):
        validate_compensation(ctype, 25_000)
        with pytest.raises(ValueError):
            validate_compensation(ctype, 0)

    def test_free_item_value_is_optional(self):
        validate_compensation("free-item", 0)
        validate_compensation("free-item", 35_000)

    def test_negative_and_unknown_rejected(self):
        with pytest.raises(ValueError):
            validate_compensation("discount", -1)
        with pytest.raises(ValueError):
            validate_compensation("cashback", 10)


def C(response=None, resolution=None, channel="walk-in", outlet="Jakarta",
      comp=0, currency="IDR", open_=False):
    return NS(first_response_minutes=response, resolution_minutes=resolution, channel=channel,
              outlet=outlet, compensation_value=comp, currency=currency, is_open=open_)


class TestKpiSummary:
    def test_empty(self):
        k = kpi_summary([], target_minutes=60)
        assert k["cases"] == 0
        assert k["avgFirstResponseMinutes"] is None
        assert k["withinTargetPct"] is None

    def test_response_and_resolution_stats(self):
        cases = [C(10, 120), C(30, 240), C(90, None, open_=True), C(None, None, open_=True)]
        k = kpi_summary(cases, target_minutes=60)
        assert k["cases"] == 4
        assert k["responded"] == 3
        assert k["avgFirstResponseMinutes"] == 43          # (10+30+90)/3 = 43.3 → 43
        assert k["medianFirstResponseMinutes"] == 30
        assert k["resolved"] == 2
        assert k["avgResolutionMinutes"] == 180
        assert k["medianResolutionMinutes"] == 180
        assert k["withinTargetPct"] == 66.7                 # 2 of 3 responded within 60
        assert k["open"] == 2

    def test_by_channel_and_compensation(self):
        cases = [
            C(5, channel="google-review", comp=50_000),
            C(5, channel="google-review", comp=0),
            C(5, channel="whatsapp", comp=25_000),
        ]
        k = kpi_summary(cases, target_minutes=60)
        assert k["byChannel"] == {"google-review": 2, "whatsapp": 1}
        assert k["compensationTotal"] == {"IDR": 75_000}

    def test_per_outlet(self):
        cases = [C(10, 60, outlet="Jakarta"), C(50, None, outlet="Jakarta", open_=True), C(20, 30, outlet="Bandung")]
        per = {o["outlet"]: o for o in kpi_summary(cases, target_minutes=60)["perOutlet"]}
        assert per["Jakarta"] == {"outlet": "Jakarta", "cases": 2, "open": 1,
                                  "avgFirstResponseMinutes": 30, "avgResolutionMinutes": 60}
        assert per["Bandung"]["cases"] == 1
