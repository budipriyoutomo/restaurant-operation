"""Unit tests — approval policy resolver (Todo-CMMS.md §2.2).

Purely in-memory: no database. Pins the contract for
approval_policy_service.resolve_policy_steps.
"""

from types import SimpleNamespace

from app.services.approval_policy_service import resolve_policy_steps


def policy(**kw):
    kw.setdefault("is_active", True)
    kw.setdefault("outlet", None)
    kw.setdefault("min_amount", None)
    kw.setdefault("max_amount", None)
    kw.setdefault("created_at", None)
    return SimpleNamespace(**kw)


MANAGER_ONLY = [{"order": 1, "role": "manager"}]
MANAGER_ADMIN = [{"order": 1, "role": "manager"}, {"order": 2, "role": "admin"}]
FULL_CHAIN = [
    {"order": 1, "role": "manager"},
    {"order": 2, "role": "admin"},
    {"order": 3, "role": "admin"},
]


class TestNoMatch:
    def test_empty_policy_list_returns_none(self):
        assert resolve_policy_steps([], "maintenance", 500_000) is None

    def test_wrong_type_returns_none(self):
        pols = [policy(approval_type="procurement", steps=MANAGER_ONLY)]
        assert resolve_policy_steps(pols, "maintenance", 500_000) is None

    def test_inactive_policy_ignored(self):
        pols = [policy(approval_type="maintenance", steps=MANAGER_ONLY, is_active=False)]
        assert resolve_policy_steps(pols, "maintenance", 500_000) is None

    def test_amount_below_min_returns_none(self):
        pols = [policy(approval_type="maintenance", steps=MANAGER_ADMIN, min_amount=1_000_000)]
        assert resolve_policy_steps(pols, "maintenance", 500_000) is None

    def test_amount_above_max_returns_none(self):
        pols = [policy(approval_type="maintenance", steps=MANAGER_ONLY, max_amount=1_000_000)]
        assert resolve_policy_steps(pols, "maintenance", 2_000_000) is None


class TestAmountTiers:
    def test_below_threshold_one_step(self):
        pols = [
            policy(approval_type="maintenance", steps=MANAGER_ONLY, min_amount=0, max_amount=5_000_000),
            policy(approval_type="maintenance", steps=FULL_CHAIN, min_amount=5_000_001),
        ]
        assert resolve_policy_steps(pols, "maintenance", 1_000_000) == MANAGER_ONLY

    def test_above_threshold_adds_finance_step(self):
        pols = [
            policy(approval_type="maintenance", steps=MANAGER_ONLY, min_amount=0, max_amount=5_000_000),
            policy(approval_type="maintenance", steps=FULL_CHAIN, min_amount=5_000_001),
        ]
        assert resolve_policy_steps(pols, "maintenance", 9_000_000) == FULL_CHAIN

    def test_none_amount_treated_as_zero(self):
        pols = [policy(approval_type="maintenance", steps=MANAGER_ONLY, min_amount=0, max_amount=5_000_000)]
        assert resolve_policy_steps(pols, "maintenance", None) == MANAGER_ONLY


class TestSpecificity:
    def test_outlet_specific_beats_global(self):
        pols = [
            policy(approval_type="maintenance", steps=MANAGER_ONLY, outlet=None),
            policy(approval_type="maintenance", steps=MANAGER_ADMIN, outlet="Bandung"),
        ]
        assert resolve_policy_steps(pols, "maintenance", 500_000, outlet="Bandung") == MANAGER_ADMIN

    def test_global_used_when_no_outlet_match(self):
        pols = [
            policy(approval_type="maintenance", steps=MANAGER_ONLY, outlet=None),
            policy(approval_type="maintenance", steps=MANAGER_ADMIN, outlet="Bandung"),
        ]
        assert resolve_policy_steps(pols, "maintenance", 500_000, outlet="Jakarta") == MANAGER_ONLY


class TestNormalization:
    def test_steps_renumbered_contiguous_from_one(self):
        weird = [{"order": 5, "role": "manager"}, {"order": 9, "role": "admin"}]
        pols = [policy(approval_type="maintenance", steps=weird)]
        result = resolve_policy_steps(pols, "maintenance", 100)
        assert result == [{"order": 1, "role": "manager"}, {"order": 2, "role": "admin"}]
