"""Unit tests — spare parts pure helpers (Tier 3)."""

from app.services.parts_service import compute_line_cost


class TestLineCost:
    def test_basic(self):
        assert compute_line_cost(3, 50_000) == 150_000

    def test_zero_quantity(self):
        assert compute_line_cost(0, 50_000) == 0

    def test_zero_cost(self):
        assert compute_line_cost(5, 0) == 0
