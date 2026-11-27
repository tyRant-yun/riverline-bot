"""Controlled line tests; these are not a replay of the platform screenshots."""
from hu_bot.core import normalize
from hu_bot.config import Config
from hu_bot.engine import Engine
from hu_bot.poker import features
from hu_bot.ranges import EquityResult
from hu_bot.strategy import opponent_led_three_streets, postflop_policy
from lab.scenarios import SRP, entry, spot


def policy(hole, board, history, *, pot, to_call, equity, exact=True,
           samples=900, stack=5000, villain_stack=None):
    raw = spot(hole, board, action_history=history, pot=pot,
               to_call=to_call, your_stack=stack,
               opponent_stacks=[stack if villain_stack is None else villain_stack],
               valid_actions=["fold", "call"] if villain_stack == 0 else ["fold", "call", "raise"],
               max_raise=0 if villain_stack == 0 else stack)
    state = normalize(raw)
    hand = features(state.hero_cards, state.board)
    result = EquityResult(equity, samples, 0 if exact else .5, exact, False,
                          "controlled_fixture")
    return postflop_policy(state, hand, result)


def test_paired_turn_two_pair_does_not_value_reraise():
    first = SRP + [entry(1, "raise", 270, "turn")]
    decision = policy("Tc Qs", "6s 3s Td 3h", first,
                      pot=1000, to_call=270, equity=.81)
    assert not any(label.startswith("RAISE") or label == "ALL_IN"
                   for label in decision.weights)


def test_paired_turn_multi_raise_folds_even_if_broad_range_equity_is_high():
    history = SRP + [entry(1, "raise", 270, "turn"),
                     entry(0, "raise", 1012, "turn"),
                     entry(1, "raise", 2253, "turn")]
    decision = policy("Tc Qs", "6s 3s Td 3h", history,
                      pot=4000, to_call=1241, equity=.81)
    assert decision.weights == {"FOLD": 1}
    assert decision.reason == "paired_board_multi_raise_fold"


def triple_barrel_history():
    return (SRP + [entry(1, "raise", 540, "flop"),
                   entry(0, "call", 540, "flop"),
                   entry(1, "raise", 1403, "turn"),
                   entry(0, "call", 1403, "turn"),
                   entry(1, "raise", 5164, "river")])


def test_weak_two_pair_folds_to_paired_dry_triple_barrel():
    decision = policy("Ac 4d", "Jh As Jc 9d 3d", triple_barrel_history(),
                      pot=14836, to_call=5164, equity=.36, stack=5164)
    assert decision.weights == {"FOLD": 1}
    assert decision.reason == "paired_board_triple_barrel_fold"


def test_budget_fallback_cannot_justify_expensive_triple_barrel_call():
    decision = policy("Ac 4d", "Jh As Jc 9d 3d", triple_barrel_history(),
                      pot=14836, to_call=5164, equity=.5, exact=False,
                      samples=0, stack=5164)
    assert decision.weights == {"FOLD": 1}


def test_reliable_strong_equity_can_still_defend_and_full_house_is_not_capped():
    history = triple_barrel_history()
    strong = policy("Ac 4d", "Jh As Jc 9d 3d", history,
                    pot=14836, to_call=5164, equity=.7, stack=5164)
    assert strong.weights == {"CALL": 1}
    full_house = policy("Ad Jd", "Jh As Jc 9d 3d", history,
                        pot=14836, to_call=5164, equity=.9, stack=5164)
    assert full_house.weights.get("FOLD", 0) == 0
    multi_raise = history + [entry(0, "raise", 7000, "river"),
                             entry(1, "raise", 9000, "river")]
    full_house_after_reraise = policy("Ad Jd", "Jh As Jc 9d 3d", multi_raise,
                                     pot=20000, to_call=2000, equity=.9,
                                     stack=5000)
    assert full_house_after_reraise.reason == "value_raise"
    assert full_house_after_reraise.weights.get("ALL_IN") == .85


def test_unpaired_board_value_raise_remains_available():
    history = SRP + [entry(1, "raise", 270, "turn")]
    decision = policy("Tc Qs", "6s 3s Td Qh", history,
                      pot=1000, to_call=270, equity=.81)
    assert decision.weights.get("RAISE_MEDIUM") == .8


def test_v15_default_engine_keeps_dry_paired_fold(monkeypatch):
    import hu_bot.engine as engine_module

    monkeypatch.setattr(engine_module, "equity", lambda *args, **kwargs:
                        EquityResult(.36, 900, 0, True, False, "controlled_fixture"))
    raw = spot("Ac 4d", "Jh As Jc 9d 3d", action_history=triple_barrel_history(),
               pot=14836, to_call=5164, your_stack=5164,
               opponent_stacks=[0], valid_actions=["fold", "call"])
    engine = Engine(Config(trace=False, decision_budget_ms=2000))
    action = engine.evaluate(raw)
    assert action.action == "fold"
    assert engine.last_trace["policy"] == {"FOLD": 1}
    assert engine.last_trace["reason"] == "paired_board_triple_barrel_fold"


def test_reliable_equity_keeps_profitable_non_allin_bluff_catch():
    history = [entry(0, "post_small_blind", 5), entry(1, "post_big_blind", 10),
               entry(0, "call", 5), entry(1, "raise", 5, "flop"),
               entry(0, "call", 5, "flop"), entry(1, "raise", 10, "turn"),
               entry(0, "call", 10, "turn"), entry(1, "raise", 100, "river")]
    raw = spot("Ac 4d", "Jh As Jc 9d 3d", action_history=history, pot=150,
               to_call=100, your_stack=475, opponent_stacks=[375],
               valid_actions=["fold", "call"])
    assert opponent_led_three_streets(normalize(raw))
    decision = policy("Ac 4d", "Jh As Jc 9d 3d", history, pot=150,
                      to_call=100, equity=.45, stack=475, villain_stack=375)
    assert decision.reason == "paired_board_cautious_bluffcatch"
    assert 0 < decision.weights["CALL"] <= .30
    assert decision.weights["FOLD"] > decision.weights["CALL"]
    low_bluff = policy("Ac 4d", "Jh As Jc 9d 3d", history, pot=150,
                       to_call=100, equity=.10, stack=475, villain_stack=375)
    assert low_bluff.weights == {"FOLD": 1}
    low_sample = policy("Ac 4d", "Jh As Jc 9d 3d", history, pot=150,
                        to_call=100, equity=.45, exact=False, samples=0,
                        stack=475, villain_stack=375)
    assert low_sample.reason == "paired_board_triple_barrel_fold"


def test_three_checkraises_are_not_three_direct_barrels():
    history = [entry(0, "post_small_blind", 5), entry(1, "post_big_blind", 10),
               entry(0, "call", 5), entry(1, "check", 0, "flop"),
               entry(0, "raise", 5, "flop"), entry(1, "raise", 10, "flop"),
               entry(0, "call", 5, "flop"), entry(1, "check", 0, "turn"),
               entry(0, "raise", 10, "turn"), entry(1, "raise", 20, "turn"),
               entry(0, "call", 10, "turn"), entry(1, "check", 0, "river"),
               entry(0, "raise", 20, "river"), entry(1, "raise", 100, "river")]
    raw = spot("Ac 4d", "Jh As Jc 9d 3d", action_history=history, pot=200,
               to_call=80, your_stack=440, opponent_stacks=[360],
               valid_actions=["fold", "call"])
    assert not opponent_led_three_streets(normalize(raw))
    decision = policy("Ac 4d", "Jh As Jc 9d 3d", history, pot=200,
                      to_call=80, equity=.35, stack=440, villain_stack=360)
    assert decision.reason == "marginal_defend"
    assert decision.weights["CALL"] > .5


def test_cheap_short_allin_calls_with_exact_positive_equity():
    history = [entry(0, "post_small_blind", 5), entry(1, "post_big_blind", 10),
        entry(0, "raise", 500), entry(1, "call", 490),
        entry(1, "raise", 379, "flop"), entry(0, "call", 379, "flop"),
        entry(1, "raise", 30, "turn"), entry(0, "raise", 60, "turn"),
        entry(1, "raise", 90, "turn"), entry(0, "raise", 120, "turn"),
        entry(1, "raise", 121, "turn")]
    decision = policy("Tc Qs", "6s 3s Td 3h", history, pot=1999,
                      to_call=1, equity=2/44, stack=1, villain_stack=0)
    assert decision.weights == {"CALL": 1}
    assert decision.reason == "paired_board_priced_allin_call"
    thin_edge = policy("Tc Qs", "6s 3s Td 3h", history, pot=1999,
                       to_call=1, equity=.01, stack=1, villain_stack=0)
    assert thin_edge.weights == {"CALL": 1}
    uncertain = policy("Tc Qs", "6s 3s Td 3h", history, pot=1999,
                       to_call=1, equity=.5, exact=False, samples=0,
                       stack=1, villain_stack=0)
    assert uncertain.weights == {"FOLD": 1}


def test_default_engine_preserves_cautious_non_allin_mix(monkeypatch):
    import hu_bot.engine as engine_module

    history = [entry(0, "post_small_blind", 5), entry(1, "post_big_blind", 10),
               entry(0, "call", 5), entry(1, "raise", 5, "flop"),
               entry(0, "call", 5, "flop"), entry(1, "raise", 10, "turn"),
               entry(0, "call", 10, "turn"), entry(1, "raise", 100, "river")]
    monkeypatch.setattr(engine_module, "equity", lambda *args, **kwargs:
                        EquityResult(.45, 900, 0, True, False, "controlled_fixture"))
    raw = spot("Ac 4d", "Jh As Jc 9d 3d", action_history=history,
               pot=150, to_call=100, your_stack=475, opponent_stacks=[375],
               valid_actions=["fold", "call"], min_raise=0, max_raise=0)
    engine = Engine(Config(trace=False, decision_budget_ms=450))
    engine.evaluate(raw)
    assert engine.last_trace["reason"] == "paired_board_cautious_bluffcatch"
    assert 0 < engine.last_trace["policy"]["CALL"] <= .30
    assert engine.last_trace["fallback"] is None
