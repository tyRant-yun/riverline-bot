from hu_bot.config import Config
from hu_bot.engine import Engine
from hu_bot.memory import OpponentModel
from hu_bot.core import normalize
from hu_bot.terminal import guaranteed_royal_call
from lab.scenarios import spot, entry, SRP, BLINDS


def test_public_royal_engine_never_folds_positive_tie_ev():
    state = spot("7h 2d", "As Ks Qs Js Ts", pot=650, to_call=600,
        opponent_stacks=[375], valid_actions=["fold", "call", "raise"],
        min_raise=975, max_raise=975, action_history=SRP+[entry(1, "raise", 600, "river")])
    engine = Engine(Config(trace=False))
    action = engine.decide(state)
    assert action.action == "call"
    assert engine.last_trace["policy"] == {"CALL": 1.}
    assert engine.last_trace["terminal_decision"]["tie_payoff"] == 25
    assert not engine.last_trace["fallback"]


def test_limp_reraise_observation_cannot_contaminate_regular_threebet():
    model = OpponentModel()
    history = BLINDS+[entry(0, "call", 5), entry(1, "raise", 35),
                       entry(0, "raise", 100), entry(1, "fold")]
    model.observe_hand(1, {"action_history": history}, hero_seat=0, dealer_seat=0)
    assert "fold_to_3bet" not in model.stats
    assert model.stats["fold_to_limp_reraise"].success == 1


def test_general_terminal_experiment_is_default_off():
    assert Config().terminal_ev_enabled is False


def test_royal_strict_profit_does_not_assume_receiving_an_odd_chip():
    state = spot("7h 2d", "As Ks Qs Js Ts", pot=101, to_call=100,
        opponent_stacks=[0], valid_actions=["fold", "call"],
        min_raise=0, max_raise=0, action_history=[entry(1, "raise", 100, "river")])
    policy, detail = guaranteed_royal_call(normalize(state))
    assert policy is None and detail["tie_payoff"] == 0
