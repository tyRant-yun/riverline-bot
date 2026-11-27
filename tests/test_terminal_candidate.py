from types import SimpleNamespace

from hu_bot.strategy import postflop_policy
from hu_bot.terminal import guaranteed_royal_call, public_royal_flush, terminal_decision


def state(*, pot=150, call=100, hero_stack=100, villain_stack=0, hero_bet=0,
          villain_bet=100, in_position=False, history=None, valid_actions=("fold", "call")):
    if history is None:
        history = ({"seat": 1, "action": "all_in", "amount": villain_bet, "phase": "turn"},)
    return SimpleNamespace(num_players=2, pot=pot, to_call=call, hero_stack=hero_stack,
                           villain_stack=villain_stack, hero_bet=hero_bet,
                           villain_bet=villain_bet, street="turn", hero_seat=0,
                           in_position=in_position, action_history=history,
                           valid_actions=valid_actions)


def eq(value, stderr=0.0, exact=False, method="monte_carlo", samples=900):
    return SimpleNamespace(equity=value, stderr=stderr, exact=exact, method=method,
                           samples=samples, nuts=False)


def label(policy):
    return None if policy is None else next(iter(policy.weights))


def test_minimal_original_counterexample_haircut_folds_positive_terminal_ev():
    # A 50-chip pre-pot plus a 100-chip shove gives P=150,C=100.  Existing OOP
    # logic makes .42*.86-.40 < -.03 and folds.  Once both stacks are committed,
    # actual call EV is .42*(150+100)-100 = +5.
    old_state = SimpleNamespace(to_call=100, pot=150, street="turn", in_position=False,
                                hero_stack=100, spr=1, node=SimpleNamespace(street_raises=0))
    feature = SimpleNamespace(made="AIR", draws=(), board_only=False, strength=.1, wet=False, blocker=False)
    old = postflop_policy(old_state, feature, eq(.42, exact=True))
    candidate, detail = terminal_decision(state(), eq(.42, exact=True), enabled=True)
    assert old.weights == {"FOLD": 1}
    assert label(candidate) == "CALL" and round(detail["point_ev"], 8) == 5


def test_positive_lower_ev_calls():
    policy, detail = terminal_decision(state(), eq(.50, .02), enabled=True)
    assert label(policy) == "CALL" and detail["ev_interval"][0] > 0


def test_negative_upper_ev_folds():
    policy, detail = terminal_decision(state(), eq(.30, .02), enabled=True)
    assert label(policy) == "FOLD" and detail["ev_interval"][1] < 0


def test_interval_crossing_zero_keeps_original_policy():
    policy, detail = terminal_decision(state(), eq(.40, .02), enabled=True)
    assert policy is None and detail["reason"].startswith("interval_crosses")


def test_ip_and_oop_allins_have_identical_terminal_result():
    oop = terminal_decision(state(in_position=False), eq(.50, .02), enabled=True)
    ip = terminal_decision(state(in_position=True), eq(.50, .02), enabled=True)
    assert label(oop[0]) == label(ip[0]) == "CALL"
    assert oop[1]["ev_interval"] == ip[1]["ev_interval"]


def test_non_allin_node_does_not_intervene():
    policy, detail = terminal_decision(state(hero_stack=300, villain_stack=300), eq(.80, .01), enabled=True)
    assert policy is None and detail["reason"] == "future_betting_possible"


def test_uncalled_excess_hidden_by_normalization_is_rejected():
    # History says villain put in 200 while a truncated to_call of 100 let
    # normalization reconstruct hero_bet=100.  The 100 excess cannot be priced.
    s = state(call=100, hero_stack=100, hero_bet=100, villain_bet=200, pot=300,
              history=({"seat": 0, "action": "raise", "amount": 50, "phase": "preflop"},
                       {"seat": 1, "action": "call", "amount": 50, "phase": "preflop"},
                       {"seat": 1, "action": "all_in", "amount": 200, "phase": "turn"}))
    policy, detail = terminal_decision(s, eq(.80, .01), enabled=True)
    assert policy is None and detail["reason"] == "history_bet_totals_not_confirmed"


def test_exact_flag_keeps_range_uncertainty_visible():
    _, detail = terminal_decision(state(), eq(.40, 0, exact=True, method="river_exact_vs_heuristic_range"), enabled=True)
    assert detail["exact_against_supplied_range"]
    assert detail["range_uncertainty"].startswith("unquantified")


def test_disabled_by_default():
    policy, detail = terminal_decision(state(), eq(.9), enabled=False)
    assert policy is None and detail["status"] == "disabled"


def test_public_royal_flush_checks_actual_board_rank_and_suit():
    assert public_royal_flush(("As", "Ks", "Qs", "Js", "Ts"))
    assert not public_royal_flush(("As", "Ks", "Qs", "Js", "9s"))
    assert not public_royal_flush(("As", "Kd", "Qs", "Js", "Ts"))


def test_guaranteed_royal_call_uses_only_a_real_public_royal():
    royal = state(pot=650, call=600, hero_stack=600, villain_stack=0,
                  villain_bet=600, history=({"seat": 1, "action": "all_in", "amount": 600, "phase": "river"},))
    royal.street, royal.board = "river", ("As", "Ks", "Qs", "Js", "Ts")
    policy, detail = guaranteed_royal_call(royal)
    assert label(policy) == "CALL" and detail["tie_payoff"] == 25
    royal.board = ("2s", "3s", "4s", "5s", "6s")
    policy, detail = guaranteed_royal_call(royal)
    assert policy is None and detail["reason"] == "not_public_river_royal_flush"
    royal.board, royal.pot = ("As", "Ks", "Qs", "Js", "Ts"), 600
    policy, detail = guaranteed_royal_call(royal)
    assert policy is None and detail["reason"] == "royal_tie_call_not_strictly_better"
