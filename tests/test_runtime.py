from dataclasses import replace
import random
from time import perf_counter

import pytest

from bot import HUBot
from hu_bot.config import Config
from hu_bot.core import normalize, Policy
from hu_bot.engine import Engine
from hu_bot.memory import OpponentModel
from hu_bot.poker import DECK, features
from hu_bot.ranges import posterior, equity
from hu_bot.strategy import legalize, exploit
from lab.scenarios import BLINDS, SRP, entry, spot, scenarios


def test_position_depth_blinds_and_ante():
    s = normalize(spot())
    assert s.position == "BTN" and s.in_position and s.effective_stack_bb == 100
    h = [entry(0, "post_small_blind", 25), entry(1, "post_big_blind", 50),
         entry(0, "post_ante", 10), entry(1, "post_ante", 10)]
    s = normalize(spot(action_history=h, pot=95, your_stack=965,
                       opponent_stacks=[940], to_call=25), 10)
    assert s.big_blind == 50 and s.hero_bet == 25 and s.villain_bet == 50
    assert s.effective_stack_bb == 19.8
    assert normalize(spot(action_history=[entry(1, "post_big_blind", 3)]), 50).big_blind == 50


def test_nodes():
    spots = scenarios()
    assert normalize(spots["btn_premium"]).node.id == "PREFLOP_BTN_FIRST_IN"
    assert normalize(spots["bb_vs_minraise"]).node.id == "PREFLOP_BB_VS_OPEN"
    assert normalize(spots["btn_vs_3bet"]).node.id == "PREFLOP_BTN_VS_3BET"


def test_raise_total_includes_previous_contribution_and_call():
    s = normalize(spot(board="As 8c 4d", pot=350, to_call=150,
        action_history=SRP+[entry(0, "raise", 75, "flop"), entry(1, "raise", 225, "flop")],
        min_raise=375, max_raise=975, valid_actions=["fold", "call", "raise"]))
    action, _ = legalize("RAISE_SMALL", s)
    assert action.amount == 475  # 75 + 150 + .5 * (350 + 150)


def test_short_allin_and_no_raise_reopening():
    s = normalize(spot(min_raise=17, max_raise=17))
    assert legalize("ALL_IN", s)[0].to_dict() == {"action": "raise", "amount": 17}
    s = normalize(spot(valid_actions=["fold", "call"], min_raise=0, max_raise=0))
    assert legalize("ALL_IN", s)[0].action == "call"


def test_legalizer_fuzz_5000():
    rng = random.Random(19)
    labels = ["FOLD", "CHECK", "CALL", "BET_SMALL", "BET_LARGE", "BET_OVERBET",
              "RAISE_SMALL", "RAISE_MEDIUM", "ALL_IN"]
    for _ in range(5000):
        min_raise = rng.randint(1, 1000)
        max_raise = rng.randint(min_raise, 2000)
        valid = rng.choice([["check"], ["check", "raise"], ["fold", "call"], ["fold", "call", "raise"]])
        s = normalize(spot(min_raise=min_raise, max_raise=max_raise, valid_actions=valid))
        action, _ = legalize(rng.choice(labels), s)
        assert action.action in valid
        assert action.action != "raise" or min_raise <= action.amount <= max_raise


@pytest.mark.parametrize("name", list(scenarios()))
def test_scenarios(name):
    engine = Engine(Config(trace=False))
    s = scenarios()[name]
    action = engine.evaluate(s)
    t = engine.last_trace
    assert action.action in s.valid_actions
    assert abs(sum(t["policy"].values())-1) < 1e-9
    assert t["elapsed_ms"] < 1000
    assert not t["fallback"]
    if name in {"btn_premium", "river_nuts"}:
        assert t["policy"].get("CHECK", 0) == 0
        assert t["policy"].get("FOLD", 0) == 0
    if name in {"flop_second_pair", "turn_giveup", "river_giveup"}:
        assert t["policy"].get("CHECK", 0) >= .75
    if name == "river_board_royal":
        assert t["policy"] == {"CHECK": 1}
        assert t["equity"]["equity"] == pytest.approx(.5)


def test_range_blockers_conditioning_and_exact_river():
    raw = spot("Ah 7d", "As 8c 4d 2h 9s")
    s = normalize(raw)
    passive = posterior(s, 1, perf_counter()+5)
    pressured = normalize(replace(raw, action_history=SRP+[entry(1, "raise", 100, "river")]))
    active = posterior(pressured, 1, perf_counter()+5)
    assert all(not set(c).intersection(s.hero_cards+s.board) for c in active.weights)
    weak = tuple(sorted(("6c", "3h"), key=DECK.index))
    assert active.weights[weak] < passive.weights[weak]
    eq = equity(s, passive, random.Random(1), perf_counter()+5)
    eq2 = equity(pressured, active, random.Random(1), perf_counter()+5)
    assert eq.exact and eq2.exact and eq2.equity < eq.equity


def test_rng_repeatable_and_match_local_reset():
    state = scenarios()["flop_combo_draw"]
    a, b = Engine(Config(trace=False)), Engine(Config(trace=False))
    assert a.evaluate(state) == b.evaluate(state)
    for field in ("random", "rng_seed", "base_policy", "equity"):
        assert a.last_trace[field] == b.last_trace[field]
    a.opponent.stat("btn_open").observe(True)
    a.reset("new-match")
    assert not a.opponent.stats


def test_opportunities_dedup_timeouts_and_bounded_exploit():
    model = OpponentModel()
    history = BLINDS+[entry(0, "raise", 20), entry(1, "fold")]
    model.observe_hand(1, {"action_history": history}, 0, 0)
    model.observe_hand(1, {"action_history": history}, 0, 0)
    assert model.stats["fold_vs_open"].opportunities == 1
    assert model.stats["fold_vs_open"].success == 1
    assert model.stats["fold_vs_open"].mean < .5
    state = normalize(spot("7h 2d"))
    f = features(state.hero_cards, ())
    base = Policy({"FOLD": .5, "CALL": .3, "RAISE_SMALL": .2})
    _, adj = exploit(base, state, f, model, .15)
    assert adj["weight"] == 0
    for i in range(2, 102):
        model.observe_hand(i, {"action_history": history}, 0, 0)
    final, adj = exploit(base, state, f, model, .15)
    assert 0 < adj["weight"] <= .15
    assert final.weights["RAISE_SMALL"] <= .32+1e-9


def test_deadline_abort_does_not_publish_partial_range():
    state = normalize(scenarios()["river_bluff_catcher"])
    r = posterior(state, 1, perf_counter()-1)
    assert not r.complete
    assert max(r.weights.values()) == min(r.weights.values())
    engine = Engine(Config(trace=False, decision_budget_ms=1))
    action = engine.evaluate(scenarios()["flop_combo_draw"])
    assert action.action in {"check", "raise"}
    assert engine.last_trace["elapsed_ms"] < 50


def test_multiway_fallback_and_hooks():
    bot = HUBot()
    bot.engine = Engine(Config(trace=False))
    bot.on_match_start({"match_id": "x", "game_config": {"big_blind": 50},
                        "seats": [{"seat": 1, "is_self": True}]})
    bot.on_round_start({"round_id": "h", "state": {"hand_number": 1, "dealer_seat": 0}})
    bot.on_phase_change({"state": {"board": ["As", "8c", "4d"]}})
    bot.on_round_result({"round_id": "h", "result": {"action_history": BLINDS}})
    assert bot.engine.big_blind == 50
    s = spot(opponent_stacks=[1000, 1000], valid_actions=["check", "raise"])
    assert bot.decide(s).action == "check"
    assert "exactly two" in bot.engine.last_trace["fallback"]


def test_replay_budget_cutoff_is_reproducible():
    original = Engine(Config(trace=False, decision_budget_ms=1))
    state = scenarios()["river_bluff_catcher"]
    original.evaluate(state)
    replay = Engine(Config(trace=False, decision_budget_ms=1000))
    replay.evaluate(state, replay_trace=original.last_trace)
    for key in ("equity", "base_policy", "policy", "random", "action"):
        assert replay.last_trace[key] == original.last_trace[key]
