"""Unit tests — budget health math (Tier 6.3)."""

from app.services.budget_service import budget_health


class TestBudgetHealth:
    def test_under_budget(self):
        h = budget_health(1_000_000, 400_000)
        assert h["remaining"] == 600_000
        assert h["pct"] == 40.0
        assert h["overBudget"] is False
        assert h["warning"] is False

    def test_warning_at_80_percent(self):
        h = budget_health(1_000_000, 800_000)
        assert h["warning"] is True
        assert h["overBudget"] is False

    def test_over_budget(self):
        h = budget_health(1_000_000, 1_200_000)
        assert h["overBudget"] is True
        assert h["warning"] is True
        assert h["remaining"] == -200_000

    def test_zero_budget_with_spend(self):
        h = budget_health(0, 50_000)
        assert h["pct"] == 100.0
        assert h["overBudget"] is False   # no budget set, don't flag over
