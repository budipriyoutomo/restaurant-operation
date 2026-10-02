"""Unit tests — enum parity between Python and frontend/lib/types.ts (Todo-Pilot §13).

Written before the implementation (TDD). Guards against a repeat of `on-hold`
existing on one side only: every string-literal union in types.ts must be
mapped to its Python source(s), and the value sets must be identical.
"""

import enum
from pathlib import Path
from typing import Literal

import pytest

from scripts.check_enum_parity import PAIRS, TS_ONLY, check, parse_ts_unions, values_of

TYPES_TS = Path(__file__).resolve().parents[3] / "frontend" / "lib" / "types.ts"


# ---------------------------------------------------------------------------
# Parsing types.ts
# ---------------------------------------------------------------------------

class TestParseTsUnions:
    def test_single_line(self):
        src = "export type Priority = 'critical' | 'high' | 'low'\n"
        assert parse_ts_unions(src) == {"Priority": {"critical", "high", "low"}}

    def test_multi_line_with_comments(self):
        src = """
export type IssueStatus =
  | 'open'
  | 'in-progress'   // hyphen, not underscore
  /* block comment */
  | 'cancelled'   // only via POST /api/issues/{id}/cancel

export interface Foo { status: IssueStatus }
"""
        assert parse_ts_unions(src) == {"IssueStatus": {"open", "in-progress", "cancelled"}}

    def test_alias_resolves_to_target(self):
        src = "export type A = 'x' | 'y'\nexport type B = A\n"
        assert parse_ts_unions(src) == {"A": {"x", "y"}, "B": {"x", "y"}}

    def test_double_quotes(self):
        assert parse_ts_unions('export type T = "a" | "b"\n') == {"T": {"a", "b"}}

    def test_non_literal_types_are_ignored(self):
        src = "export type UserRole = string\nexport type Id = number | null\n"
        assert parse_ts_unions(src) == {}

    def test_mixed_union_is_not_truncated(self):
        # 'a' | string is not an enum; never report it as {'a'}
        assert parse_ts_unions("export type T = 'a' | string\n") == {}


# ---------------------------------------------------------------------------
# Reading Python value sources
# ---------------------------------------------------------------------------

class _Color(str, enum.Enum):
    red = "red"
    dark_blue = "dark-blue"


class TestValuesOf:
    def test_enum_class(self):
        assert values_of(_Color) == {"red", "dark-blue"}

    def test_collections(self):
        assert values_of(("a", "b")) == {"a", "b"}
        assert values_of({"a"}) == {"a"}
        assert values_of(frozenset({"a"})) == {"a"}

    def test_literal(self):
        assert values_of(Literal["x", "y"]) == {"x", "y"}

    def test_sqlalchemy_enum_column(self):
        from app.models.campaign import Campaign
        assert values_of(Campaign.status) == {"draft", "active", "completed", "cancelled"}

    def test_unsupported_raises(self):
        with pytest.raises(TypeError):
            values_of(42)


# ---------------------------------------------------------------------------
# Comparison
# ---------------------------------------------------------------------------

def _src(**objs):
    """Fake resolver: ref name → object."""
    return lambda ref: objs[ref]


class TestCheck:
    def test_identical_sets_pass(self):
        ts = {"Status": {"a", "b"}}
        assert check(ts, {"Status": ["S"]}, set(), _src(S=("a", "b"))) == []

    def test_value_missing_on_either_side(self):
        ts = {"Status": {"a", "on-hold"}}
        problems = check(ts, {"Status": ["S"]}, set(), _src(S=("a", "b")))
        assert len(problems) == 1
        assert "Status" in problems[0] and "S" in problems[0]
        assert "only in TS: ['on-hold']" in problems[0]
        assert "only in Python: ['b']" in problems[0]

    def test_every_python_source_is_checked(self):
        ts = {"Status": {"a", "b"}}
        problems = check(ts, {"Status": ["S1", "S2"]}, set(), _src(S1=("a", "b"), S2=("a",)))
        assert len(problems) == 1 and "S2" in problems[0]

    def test_unmapped_ts_union_is_a_problem(self):
        # a new enum added to types.ts must be mapped (or declared TS-only) — no silent gaps
        problems = check({"New": {"x"}}, {}, set(), _src())
        assert problems == ["types.ts: New has no Python mapping — add it to PAIRS or TS_ONLY"]

    def test_ts_only_is_skipped(self):
        assert check({"UiOnly": {"x"}}, {}, {"UiOnly"}, _src()) == []

    def test_mapped_type_missing_from_ts(self):
        problems = check({}, {"Gone": ["S"]}, set(), _src(S=("a",)))
        assert problems == ["types.ts: Gone not found (renamed or removed?)"]

    def test_unresolvable_python_ref(self):
        def boom(ref):
            raise AttributeError("no such thing")
        problems = check({"T": {"a"}}, {"T": ["app.x:Y"]}, set(), boom)
        assert len(problems) == 1 and "app.x:Y" in problems[0]


# ---------------------------------------------------------------------------
# The real repository
# ---------------------------------------------------------------------------

def test_mapping_tables_do_not_overlap():
    assert not set(PAIRS) & TS_ONLY


@pytest.mark.skipif(not TYPES_TS.exists(), reason="frontend/ not checked out next to backend/")
def test_repository_enums_are_in_sync():
    from scripts.check_enum_parity import resolve
    problems = check(parse_ts_unions(TYPES_TS.read_text()), PAIRS, TS_ONLY, resolve)
    assert problems == [], "\n".join(problems)


@pytest.mark.skipif(not TYPES_TS.exists(), reason="frontend/ not checked out next to backend/")
def test_issue_wo_approval_statuses_are_covered():
    # the three named in Todo-Pilot §13 must never drop out of the mapping
    for name in ("IssueStatus", "TaskStatus", "WorkOrderStatus", "ApprovalStatus", "ApprovalStepStatus"):
        assert name in PAIRS
