from hu_bot.memory import OpponentModel
from hu_bot.opponent_actions import MAX_ACTION_CELLS
from lab.scenarios import SRP, entry


def observe(model, key, actions, *, hero=0, dealer=0):
    model.observe_hand(key, {"action_history": SRP + actions, "showdown": []}, hero, dealer)


def test_unopened_and_facing_bet_have_separate_mutually_exclusive_denominators():
    model = OpponentModel()
    observe(model, 1, [entry(1, "check", phase="flop"),
                       entry(0, "raise", 20, "flop"),
                       entry(1, "call", 20, "flop")])
    observe(model, 2, [entry(1, "raise", 60, "flop"),
                       entry(0, "fold", phase="flop")])
    unopened = model.action_cells[("flop", "SRP", "BB", "unopened", None)]
    facing = model.action_cells[("flop", "SRP", "BB", "facing_first_bet", "small")]
    assert unopened.opportunities == 2
    assert unopened.counts == {"check": 1, "bet": 1}
    assert unopened.size_counts == {"over": 1}
    assert facing.opportunities == 1
    assert facing.counts == {"call": 1}
    assert facing.size_counts == {}


def test_price_bucket_and_facing_raise_are_based_on_prior_action():
    model = OpponentModel()
    observe(model, 1, [entry(0, "raise", 25, "turn"),
                       entry(1, "fold", phase="turn")])
    observe(model, 2, [entry(1, "raise", 25, "flop"),
                       entry(0, "raise", 75, "flop"),
                       entry(1, "all_in", 75, "flop")])
    # 25 into the 50-chip pot is medium; all_in to the existing table bet is a call.
    first = model.action_cells[("turn", "SRP", "BB", "facing_first_bet", "medium")]
    raised = model.action_cells[("flop", "SRP", "BB", "facing_raise", "medium")]
    assert first.counts == {"fold": 1}
    assert raised.counts == {"call": 1}
    assert raised.size_counts == {}


def test_conditional_raise_size_and_position_are_recorded():
    model = OpponentModel()
    # Villain is BTN here and raises a 20-chip first bet to 80.
    observe(model, 1, [entry(0, "raise", 20, "river"),
                       entry(1, "raise", 80, "river"),
                       entry(0, "fold", phase="river")], hero=0, dealer=1)
    key = ("river", "SRP", "BTN", "facing_first_bet", "small")
    cell = model.action_cells[key]
    assert cell.counts == {"raise": 1}
    assert cell.size_counts == {"medium": 1}


def test_timeout_duplicate_hand_and_capacity_do_not_inflate_cells():
    model = OpponentModel(action_cell_limit=2)
    assert model.action_cell_limit == 2
    observe(model, 1, [entry(1, "check", phase="flop")])
    observe(model, 1, [entry(1, "raise", 100, "flop")])
    observe(model, 2, [{**entry(1, "raise", 100, "turn"), "is_timeout": True}])
    assert len(model.action_cells) == 1
    assert next(iter(model.action_cells.values())).opportunities == 1
    observe(model, 3, [entry(1, "check", phase="turn")])
    observe(model, 4, [entry(1, "check", phase="river")])
    assert len(model.action_cells) == 2
    assert sum(c.opportunities for c in model.action_cells.values()) == 2
    assert OpponentModel(action_cell_limit=10**9).action_cell_limit == MAX_ACTION_CELLS
