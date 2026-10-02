"""Unit tests — campaign money & results (Todo-Pilot §10). Pure, no database.

Written before the implementation (TDD).
"""

import pytest

from app.services.campaign_service import campaign_metrics, parse_legacy_budget, results_error


# ---------------------------------------------------------------------------
# Old free-text budgets → (integer amount, currency)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("raw,expected", [
    ("Rp 5.000.000", (5_000_000, "IDR")),
    ("Rp5.000.000,-", (5_000_000, "IDR")),
    ("IDR 2,500,000", (2_500_000, "IDR")),
    ("5000000", (5_000_000, "IDR")),
    ("5 juta", (5_000_000, "IDR")),
    ("1,5 juta", (1_500_000, "IDR")),
    ("1.5jt", (1_500_000, "IDR")),
    ("750rb", (750_000, "IDR")),
    ("300 ribu", (300_000, "IDR")),
    ("20k", (20_000, "IDR")),
    ("2 miliar", (2_000_000_000, "IDR")),
    ("RM 5,000", (5_000, "MYR")),           # the old form's own placeholder
    ("MYR 1,250.50", (1_251, "MYR")),       # minor units rounded away
    ("rm2000", (2_000, "MYR")),
])
def test_parse_legacy_budget(raw, expected):
    assert parse_legacy_budget(raw) == expected


@pytest.mark.parametrize("raw", [None, "", "   ", "TBD", "sesuai kebutuhan", "Rp -", "USD 100"])
def test_unparseable_budget_is_none(raw):
    assert parse_legacy_budget(raw) is None


# ---------------------------------------------------------------------------
# Results rules
# ---------------------------------------------------------------------------

class TestResultsError:
    def test_results_need_an_active_or_completed_campaign(self):
        assert results_error("draft", has_results=True) == "Results can be recorded once the campaign is active"
        assert results_error("cancelled", has_results=True) == "Results can be recorded once the campaign is active"
        assert results_error("active", has_results=True) is None
        assert results_error("completed", has_results=True) is None

    def test_actual_cost_alone_is_allowed_any_time(self):
        # money spent on a campaign that was then cancelled is still money spent
        assert results_error("cancelled", has_results=False) is None
        assert results_error("draft", has_results=False) is None


# ---------------------------------------------------------------------------
# Metrics
# ---------------------------------------------------------------------------

def M(**kw):
    base = dict(budget=None, actual_cost=None, result_transactions=None, result_revenue=None,
                baseline_transactions=None, baseline_revenue=None)
    base.update(kw)
    return campaign_metrics(**base)


class TestMetrics:
    def test_nothing_recorded(self):
        assert M() == {
            "budget_used_pct": None, "over_budget": False, "cost_per_transaction": None,
            "revenue_per_rupiah": None, "revenue_uplift_pct": None, "transaction_uplift_pct": None,
        }

    def test_budget_usage(self):
        m = M(budget=10_000_000, actual_cost=12_500_000)
        assert m["budget_used_pct"] == 125.0
        assert m["over_budget"] is True
        assert M(budget=10_000_000, actual_cost=4_000_000)["over_budget"] is False

    def test_cost_per_transaction_and_return(self):
        m = M(actual_cost=5_000_000, result_transactions=250, result_revenue=40_000_000)
        assert m["cost_per_transaction"] == 20_000
        assert m["revenue_per_rupiah"] == 8.0

    def test_uplift_against_baseline(self):
        m = M(result_transactions=300, result_revenue=60_000_000,
              baseline_transactions=200, baseline_revenue=50_000_000)
        assert m["transaction_uplift_pct"] == 50.0
        assert m["revenue_uplift_pct"] == 20.0

    def test_negative_uplift(self):
        assert M(result_revenue=40, baseline_revenue=50)["revenue_uplift_pct"] == -20.0

    def test_zero_denominators_give_none(self):
        m = M(budget=0, actual_cost=0, result_transactions=0, result_revenue=100,
              baseline_transactions=0, baseline_revenue=0)
        assert m["budget_used_pct"] is None
        assert m["cost_per_transaction"] is None
        assert m["revenue_per_rupiah"] is None
        assert m["transaction_uplift_pct"] is None
        assert m["revenue_uplift_pct"] is None
