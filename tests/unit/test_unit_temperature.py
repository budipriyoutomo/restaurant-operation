"""Pure temperature rules (Todo-Ops-Checklist Fase 2). Written before the implementation (TDD)."""

from app.services.temperature_rules import consecutive_excursions, effective_limits, excursion_severity, in_range


class TestInRange:
    def test_inclusive_and_negative(self):
        assert in_range(-22, -22, -15) is True
        assert in_range(-15, -22, -15) is True
        assert in_range(-14.9, -22, -15) is False
        assert in_range(-22.1, -22, -15) is False

    def test_open_bounds(self):
        assert in_range(30, None, 4) is False
        assert in_range(-30, None, 4) is True
        assert in_range(5, None, None) is True


class TestSeverity:
    def test_within_range_is_none(self):
        assert excursion_severity(-18, -22, -15) is None

    def test_minor_below_delta(self):
        assert excursion_severity(-13, -22, -15) == "minor"          # 2 °C above max
        assert excursion_severity(-24.5, -22, -15) == "minor"        # 2.5 °C below min

    def test_major_at_or_above_delta(self):
        assert excursion_severity(-12, -22, -15) == "major"          # exactly 3 °C
        assert excursion_severity(9, 0, 4) == "major"
        assert excursion_severity(-3, 0, 4) == "major"

    def test_delta_is_configurable(self):
        assert excursion_severity(-12, -22, -15, major_delta=5) == "minor"


class TestConsecutive:
    def test_latest_n_all_out(self):
        assert consecutive_excursions([False, False, True], 2) is True     # newest first

    def test_a_normal_reading_in_between_resets(self):
        assert consecutive_excursions([False, True, False], 2) is False

    def test_not_enough_readings(self):
        assert consecutive_excursions([False], 2) is False
        assert consecutive_excursions([], 1) is False

    def test_limit_one(self):
        assert consecutive_excursions([False], 1) is True


class TestEffectiveLimits:
    def test_item_overrides_asset_per_bound(self):
        assert effective_limits(None, None, -22, -15) == (-22, -15)
        assert effective_limits(-20, None, -22, -15) == (-20, -15)
        assert effective_limits(None, -16, -22, -15) == (-22, -16)

    def test_no_asset(self):
        assert effective_limits(0, 4, None, None) == (0, 4)
