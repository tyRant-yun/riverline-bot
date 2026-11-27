"""Decision-path checks for the offline double-paired river experiment."""

import pytest
from itertools import combinations

from hu_bot.poker import DECK, evaluate
from lab.double_pair_river import (BOARD, BOARDS, action_values,
                                   hero_combos, replay, target)


def test_target_is_a_full_class_across_several_boards():
    assert [len(hero_combos(board)) for board in BOARDS] == [44, 44, 44]
    assert target(BOARD, ("Qs", "5s"))
    assert target(BOARD, ("Ts", "8s"))
    assert not target(BOARD, ("Ad", "2h"))
    for board in BOARDS:
        for hole in hero_combos(board):
            assert evaluate(tuple(board) + hole)[0] == 5


def test_full_hand_reaches_turn_checkraise_and_separate_river_decisions():
    row = replay(("Qs", "5s"), ("Ad", "2h"), "normal",
                 "medium", "call", 191101)
    assert row["hero_river_first_state"]["pot"] == 7654
    assert [(e["seat"], e["action"], e["amount"])
            for e in row["turn_prefix"]] == [
                (1, "check", 0), (0, "raise", 835),
                (1, "raise", 3131), (0, "call", 2296)]
    assert [(e["seat"], e["action"], e["amount"])
            for e in row["river_actions"]] == [
                (1, "raise", 5052), (0, "raise", 5207), (1, "call", 155)]
    assert row["chip_total"] == 19034
    assert row["hero_net"] == -9034


def test_same_legal_route_reaches_a_second_double_paired_board():
    board = BOARDS[1]
    hole = hero_combos(board)[22]
    villain = next(cards for cards in combinations(
        (card for card in DECK if card not in board and card not in hole), 2)
        if evaluate(tuple(board) + cards)[0] >= 6)
    row = replay(hole, villain, "normal", "medium", "call", 191102,
                 board=board)
    assert row["hero_river_first_state"]["pot"] == 7654
    assert row["chip_total"] == 19034


@pytest.mark.parametrize("profile", ["normal", "wide_call", "low_bluff",
                                     "high_bluff", "range_miss"])
def test_called_and_raised_ranges_are_separate(profile):
    values = action_values(BOARD, ("Qs", "5s"), profile)["actions"]["medium"]
    assert abs(values["fold_mass"] + values["call_mass"] +
               values["raise_mass"] - 1) < 1e-9
    assert values["call_mass"] > 0 and values["raise_mass"] > 0
    assert values["called_range_equity"] != values["raised_range_equity"]
    assert values["extra_call"] == 155
