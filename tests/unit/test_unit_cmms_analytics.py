"""Unit tests — CMMS analytics pure metric functions (Tier 3)."""

import pytest

from app.services.cmms_analytics_service import (
    compute_mttr,
    compute_mtbf,
    compute_uptime_pct,
    repair_vs_replace,
)


class TestMTTR:
    def test_empty_is_zero(self):
        assert compute_mttr([]) == 0.0

    def test_average(self):
        assert compute_mttr([2.0, 4.0]) == pytest.approx(3.0)

    def test_single(self):
        assert compute_mttr([5.0]) == pytest.approx(5.0)


class TestMTBF:
    def test_no_failures_is_zero(self):
        assert compute_mtbf(1000, 100, 0) == 0.0

    def test_basic(self):
        # (100 - 10) / 3 = 30
        assert compute_mtbf(100, 10, 3) == pytest.approx(30.0)

    def test_downtime_exceeds_period_clamps_to_zero(self):
        assert compute_mtbf(10, 50, 2) == 0.0


class TestUptime:
    def test_basic(self):
        assert compute_uptime_pct(100, 10) == pytest.approx(90.0)

    def test_zero_period_returns_full(self):
        assert compute_uptime_pct(0, 0) == 100.0

    def test_downtime_exceeds_period_clamps_to_zero(self):
        assert compute_uptime_pct(100, 250) == 0.0

    def test_no_downtime_is_hundred(self):
        assert compute_uptime_pct(500, 0) == 100.0


class TestRepairVsReplace:
    def test_unknown_purchase_cost_returns_none(self):
        assert repair_vs_replace(900_000, None) is None

    def test_zero_purchase_cost_returns_none(self):
        assert repair_vs_replace(900_000, 0) is None

    def test_below_ratio_false(self):
        assert repair_vs_replace(400_000, 1_000_000, 0.5) is False

    def test_at_ratio_true(self):
        assert repair_vs_replace(500_000, 1_000_000, 0.5) is True

    def test_above_ratio_true(self):
        assert repair_vs_replace(700_000, 1_000_000, 0.5) is True
