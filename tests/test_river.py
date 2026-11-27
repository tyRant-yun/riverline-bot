from dataclasses import replace
import math
from time import perf_counter

import pytest

from hu_bot.config import Config
from hu_bot.core import normalize
from hu_bot.engine import Engine
from hu_bot.memory import OpponentModel
from hu_bot.ranges import posterior
from hu_bot.responses import evidence, response_keys
from hu_bot.river import bet_fold_ev, calibrated_rows, call_ev, plan
from lab.scenarios import SRP, entry, scenarios, spot
from lab.sessions import configuration, evaluate_sessions, interval, merge_sessions


def observed_model(action, count=100, amount=16, timeout=False):
    model = OpponentModel()
    for hand in range(count):
        response = entry(1, action, 16 if action == "call" else 40 if action == "raise" else 0, "river")
        response["is_timeout"] = timeout
        history = SRP+[entry(1, "check", 0, "river"), entry(0, "raise", amount, "river"), response]
        model.observe_hand(hand, {"action_history": history}, 0, 0)
    return model


def test_terminal_ev_arithmetic_and_sunk_cost_convention():
    assert call_ev(150, 50, .25) == 0
    assert bet_fold_ev(100, 75, .5, .5, 0, .3) == 50
    assert bet_fold_ev(100, 100, .5, .5, 0, 0) == 0
    assert bet_fold_ev(100, 50, 0, 0, 1, 1) == -50


@pytest.mark.parametrize("action,fold,raise_", [("fold", 1, 0), ("call", 0, 0), ("raise", 0, 1)])
def test_response_opportunity_uses_faced_bet_not_response_size(action, fold, raise_):
    model = observed_model(action, count=12)
    for key in response_keys("SRP", False, 16/50):
        assert model.stats[key+":fold"].opportunities == 12
        assert model.stats[key+":fold"].success == 12*fold
        assert model.stats[key+":raise"].success == 12*raise_
    assert evidence(model, "SRP", False, .32)["n"] == 12
    assert evidence(model, "3BET", False, .32) is None
    # Deduplication applies to all old and new statistics.
    before = model.summary()
    model.observe_hand(11, {"action_history": SRP}, 0, 0)
    assert model.summary() == before


def test_timeouts_and_frozen_learning_do_not_pollute_model():
    assert not any(k.startswith("response:") for k in observed_model("fold", timeout=True).stats)
    frozen = OpponentModel(enabled=False)
    frozen.observe_hand(1, {"action_history": SRP}, 0, 0)
    assert not frozen.stats
    assert not frozen._seen


def test_response_gates_and_hierarchical_fallback():
    assert evidence(observed_model("fold", 11), "SRP", False, .32) is None
    model = observed_model("fold", 60)
    fine = evidence(model, "SRP", False, .32)
    broad = evidence(model, "3BET", False, .32)
    global_ = evidence(model, "3BET", True, 1.25)
    assert fine["key"] == "response:river:SRP:OOP:small"
    assert broad["key"] == "response:river:small"
    assert global_["key"] == "response:river"
    assert global_["weight"] <= broad["weight"] <= fine["weight"]


def test_joint_calibration_preserves_hands_and_probability_simplex():
    rows = [[.4, 1., .8, .19, .01], [.6, 0., .1, .7, .2]]
    observed = {"fold": .05, "raise": .1, "weight": .9}
    result = calibrated_rows(rows, observed, perf_counter()+1)
    assert [r[:2] for r in result] == [r[:2] for r in rows]
    assert all(abs(sum(r[2:])-1) < 1e-10 and min(r[2:]) >= 0 for r in result)
    target_fold = .1*(.4*.8+.6*.1)+.9*.05
    assert abs(sum(r[0]*r[2] for r in result)-target_fold) < .003
    assert result[0][2] > result[1][2]  # Preserve weak-hand relative propensity to fold.


def river_ranges(raw):
    s = normalize(raw)
    return s, posterior(s, 1-s.hero_seat, perf_counter()+5), posterior(s, s.hero_seat, perf_counter()+5)


def test_overfolder_enables_evidence_backed_bluff_station_does_not():
    s, vr, hr = river_ranges(scenarios()["river_giveup"])
    cold, cold_detail = plan(s, vr, hr, OpponentModel(), perf_counter()+5)
    station, station_detail = plan(s, vr, hr, observed_model("call"), perf_counter()+5)
    folder, folder_detail = plan(s, vr, hr, observed_model("fold"), perf_counter()+5)
    assert cold.weights == station.weights == {"CHECK": 1}
    assert folder.weights.get("CHECK", 0) == 0
    assert folder_detail["complete"] and station_detail["complete"] and cold_detail["complete"]
    assert any(c.get("evidence") for c in folder_detail["candidates"])


def test_deadline_discards_whole_plan_and_does_not_publish_prefix():
    s, vr, hr = river_ranges(scenarios()["river_giveup"])
    policy, detail = plan(s, vr, hr, OpponentModel(), perf_counter()-1)
    assert policy is None and detail["reason"] == "deadline_atomic_discard"
    assert "candidates" not in detail


def test_ip_only_and_preflop_start_depth_gate():
    raw = scenarios()["river_giveup"]
    s, vr, hr = river_ranges(raw)
    assert plan(replace(s, in_position=False), vr, hr, OpponentModel(), perf_counter()+2)[0] is None
    # 20BB remaining after 80BB invested still started at 100BB.
    history = SRP+[entry(1, "raise", 775, "turn"), entry(0, "call", 775, "turn")]
    deep = spot("7h 2d", "As Kc Qd 9h 4s", action_history=history,
                pot=1600, your_stack=200, opponent_stacks=[200], max_raise=200)
    s, vr, hr = river_ranges(deep)
    _, detail = plan(s, vr, hr, OpponentModel(), perf_counter()+5)
    assert detail["complete"] and detail["depth_at_start_bb"] == 100
    for candidate in detail["candidates"][1:]:
        assert candidate["amount"] == 200 and candidate["raise"] == 0
    short, vr, hr = river_ranges(spot("7h 2d", "As Kc Qd 9h 4s",
                                     your_stack=175, opponent_stacks=[175], max_raise=175))
    assert plan(short, vr, hr, OpponentModel(), perf_counter()+2)[1]["reason"] == "outside_60_125bb_start"


def test_response_model_does_not_use_hero_private_cards():
    raw = scenarios()["river_giveup"]
    s, vr, hr = river_ranges(raw)
    # Hold the public ranges fixed; response probabilities must not depend on our hand.
    _, first = plan(s, vr, hr, OpponentModel(), perf_counter()+5)
    _, second = plan(replace(s, hero_cards=("Jh", "Td")), vr, hr, OpponentModel(), perf_counter()+5)
    for a, b in zip(first["candidates"][1:], second["candidates"][1:]):
        assert (a["fold"], a["call"], a["raise"]) == (b["fold"], b["call"], b["raise"])


def test_replay_full_ev_policy_and_learning_snapshot():
    original = Engine(Config(trace=False))
    original.opponent = observed_model("fold")
    raw = scenarios()["river_giveup"]
    original.evaluate(raw)
    trace = original.last_trace
    assert trace["river_ev"]["complete"]
    replay = Engine(Config(trace=False, decision_budget_ms=2000))
    replay.opponent = original.opponent
    replay.evaluate(raw, replay_trace=trace)
    for key in ("policy", "action", "random", "river_ev"):
        assert trace[key] == replay.last_trace[key]


def test_modes_disable_all_learning_not_only_generic_exploit():
    frozen, range_only, full = (configuration(mode) for mode in ("frozen", "range", "full"))
    assert not frozen.learn_opponent and not frozen.adapt_ranges and not frozen.adapt_responses
    assert range_only.learn_opponent and range_only.adapt_ranges and not range_only.adapt_responses
    assert range_only.exploit_cap == 0 and full.exploit_cap > 0


def test_session_evaluation_memory_counts_and_independent_inference_units():
    result = evaluate_sessions("call", sessions=2, hands=12, seed=12017, modes=("frozen", "full"))
    assert result["total_hands"] == 96 and result["hands_per_mode"] == 48
    assert len(result["session_pair_bb_per_100"]["full"]) == 2
    assert result["diagnostics"]["frozen"]["end_max_opportunities"] == [0]*4
    assert all(n > 1 for n in result["diagnostics"]["full"]["end_max_opportunities"])
    assert result["diagnostics"]["full"]["fallbacks"] == 0
    gain = result["paired_adaptation_gain_bb_per_100"]["full_minus_frozen"]
    diffs = [a-b for a, b in zip(result["session_pair_bb_per_100"]["full"],
                                result["session_pair_bb_per_100"]["frozen"])]
    assert gain == interval(diffs) and math.isfinite(gain["mean"])


def test_session_sharding_preserves_seed_identity_scores_and_memory():
    whole = evaluate_sessions("overfolder", 2, 3, 19001, ("full",))
    a = evaluate_sessions("overfolder", 1, 3, 19001, ("full",), session_offset=0)
    b = evaluate_sessions("overfolder", 1, 3, 19001, ("full",), session_offset=1)
    merged = merge_sessions([b, a])
    for key in ("session_indices", "sessions", "hands_per_mode", "total_hands",
                "session_pair_bb_per_100", "bb_per_100"):
        assert whole[key] == merged[key]
    assert whole["diagnostics"]["full"]["end_max_opportunities"] == merged["diagnostics"]["full"]["end_max_opportunities"]
    with pytest.raises(ValueError, match="Overlapping"):
        merge_sessions([a, a])
    b["seed"] = 1
    with pytest.raises(ValueError, match="Incompatible"):
        merge_sessions([a, b])
