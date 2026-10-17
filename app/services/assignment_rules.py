"""Who gets a new Issue nobody was assigned to (Todo-Ops-Checklist P2, keputusan #12).

Pure — no database. Candidates, in order:
  1. the rule for (category, outlet)
  2. the rule for (category, every outlet)
  3. PICs handling the category who have a user account, by name
pick_assignee takes the first candidate the caller says is eligible (active,
can work at the outlet); none → the Issue stays Unassigned.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Callable, Iterable, List, Optional, Set


@dataclass(frozen=True)
class Rule:
    category_id: uuid.UUID
    outlet_id: Optional[uuid.UUID]      # None = every outlet
    user_id: uuid.UUID


@dataclass(frozen=True)
class PicLink:
    name: str
    user_id: Optional[uuid.UUID]        # None = a contact without an account
    category_ids: Set[uuid.UUID] = field(default_factory=set)


def candidate_user_ids(rules: Iterable[Rule], pics: Iterable[PicLink],
                       category_id: uuid.UUID, outlet_id: Optional[uuid.UUID]) -> List[uuid.UUID]:
    rules = [r for r in rules if r.category_id == category_id]
    ordered: List[uuid.UUID] = []
    if outlet_id is not None:
        ordered += [r.user_id for r in rules if r.outlet_id == outlet_id]
    ordered += [r.user_id for r in rules if r.outlet_id is None]
    ordered += [p.user_id for p in sorted(pics, key=lambda p: p.name.lower())
                if p.user_id is not None and category_id in p.category_ids]
    seen: Set[uuid.UUID] = set()
    return [u for u in ordered if not (u in seen or seen.add(u))]


def pick_assignee(candidates: Iterable[uuid.UUID], eligible: Callable[[uuid.UUID], bool]) -> Optional[uuid.UUID]:
    return next((u for u in candidates if eligible(u)), None)
