from hu_bot.memory import OpponentModel
from lab.scenarios import SRP, entry


BOARD = "As 8c 4d 2h 9s".split()
KEY = "river:first:SRP:OOP:open"


def result(hole=None, bet=150):
    history = SRP+[entry(1, "raise", bet, "river"), entry(0, "call", bet, "river")]
    return {"action_history": history, "showdown": [] if hole is None else
            [{"seat": 1, "hole_cards": hole, "hand_rank": 0}], "board": BOARD}


def test_first_overbet_opportunity_and_revealed_subset_are_separate():
    model = OpponentModel()
    model.observe_hand(1, result(["7c", "6c"]), 0, 0, BOARD)
    model.observe_hand(2, result(), 0, 0, BOARD)
    assert model.stats[KEY+":bet"].opportunities == 2
    assert model.stats[KEY+":overbet"].success == 2
    assert model.stats[KEY+":revealed_weak"].opportunities == 1
    assert model.stats[KEY+":revealed_weak"].success == 1
    assert model.stats[KEY+":revealed_strong"].success == 0
    assert len(model.showdowns) == 1


def test_duplicate_or_nonshowdown_cannot_invent_revealed_strength():
    model = OpponentModel()
    model.observe_hand(1, result(["8d", "8h"]), 0, 0, BOARD)
    model.observe_hand(1, result(["7c", "6c"]), 0, 0, BOARD)
    assert model.stats[KEY+":revealed_strong"].opportunities == 1
    assert model.stats[KEY+":revealed_strong"].success == 1
    model.observe_hand(2, result(["As", "3c"]), 0, 0, BOARD)
    assert model.stats[KEY+":revealed_weak"].opportunities == 1


def test_river_check_adds_opportunity_without_overbet_success():
    model = OpponentModel()
    history = SRP+[entry(1, "check", 0, "river"), entry(0, "check", 0, "river")]
    model.observe_hand(1, {"action_history": history, "showdown": []}, 0, 0, BOARD)
    assert model.stats[KEY+":bet"].opportunities == 1
    assert model.stats[KEY+":bet"].success == 0
    assert model.stats[KEY+":overbet"].success == 0


def test_all_in_call_after_hero_river_bet_is_not_a_first_overbet():
    model = OpponentModel()
    history = SRP+[entry(0, "raise", 100, "river"), entry(1, "all_in", 100, "river")]
    model.observe_hand(1, {"action_history": history, "showdown": []}, 0, 0, BOARD)
    assert KEY+":overbet" not in model.stats
