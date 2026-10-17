"""Assignee routing for new Issues (Todo-Ops-Checklist P2, keputusan #12).

An Issue created without an assignee goes to, in order:
  1. the rule for (category, outlet);
  2. the rule for (category, every outlet);
  3. a PIC handling the category who has a user account;
  4. nobody (Unassigned).
A candidate who is inactive or cannot work at the outlet is skipped.
"""

import uuid

from app.services.assignment_rules import PicLink, Rule, candidate_user_ids, pick_assignee

CAT, OTHER_CAT = uuid.uuid4(), uuid.uuid4()
DAGO, BDG = uuid.uuid4(), uuid.uuid4()
BUDI, ANDI, SITI, RINA = (uuid.uuid4() for _ in range(4))


class TestCandidates:
    def test_outlet_rule_then_global_rule_then_pics(self):
        rules = [Rule(CAT, None, ANDI), Rule(CAT, DAGO, BUDI)]
        pics = [PicLink("Siti", SITI, {CAT})]
        assert candidate_user_ids(rules, pics, CAT, DAGO) == [BUDI, ANDI, SITI]

    def test_another_outlets_rule_is_ignored(self):
        rules = [Rule(CAT, DAGO, BUDI)]
        assert candidate_user_ids(rules, [], CAT, BDG) == []

    def test_another_categorys_rule_and_pic_are_ignored(self):
        rules = [Rule(OTHER_CAT, None, BUDI)]
        pics = [PicLink("Siti", SITI, {OTHER_CAT})]
        assert candidate_user_ids(rules, pics, CAT, DAGO) == []

    def test_pics_without_a_user_are_ignored(self):
        pics = [PicLink("Kontak luar", None, {CAT}), PicLink("Siti", SITI, {CAT})]
        assert candidate_user_ids([], pics, CAT, DAGO) == [SITI]

    def test_several_pics_in_name_order(self):
        pics = [PicLink("Rina", RINA, {CAT}), PicLink("andi", ANDI, {CAT})]
        assert candidate_user_ids([], pics, CAT, DAGO) == [ANDI, RINA]

    def test_a_user_appears_once(self):
        rules = [Rule(CAT, DAGO, BUDI), Rule(CAT, None, BUDI)]
        pics = [PicLink("Budi", BUDI, {CAT})]
        assert candidate_user_ids(rules, pics, CAT, DAGO) == [BUDI]

    def test_issue_without_outlet_uses_only_global_rules(self):
        rules = [Rule(CAT, DAGO, BUDI), Rule(CAT, None, ANDI)]
        assert candidate_user_ids(rules, [], CAT, None) == [ANDI]


class TestPick:
    def test_first_eligible_wins(self):
        assert pick_assignee([BUDI, ANDI], lambda u: True) == BUDI

    def test_ineligible_candidates_are_skipped(self):
        assert pick_assignee([BUDI, ANDI, SITI], lambda u: u == SITI) == SITI

    def test_nobody_eligible_is_none(self):
        assert pick_assignee([BUDI], lambda u: False) is None
        assert pick_assignee([], lambda u: True) is None
