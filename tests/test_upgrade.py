from dataclasses import replace
import hashlib
import json
from pathlib import Path
from time import perf_counter

import pytest

from hu_bot.core import normalize
from hu_bot.engine import Engine
from hu_bot.config import Config
from hu_bot.history import ledger, preflop_context
from hu_bot.memory import OpponentModel
from hu_bot.preflop import DATA, TABLE_SHA256, lookup, preflop_policy
from hu_bot.ranges import posterior
from lab.scenarios import BLINDS, SRP, entry, spot
from tools.generate_preflop import generate


def test_generated_tables_match_reviewable_source():
    encoded = (json.dumps(generate(), separators=(",", ":"), sort_keys=True)+"\n").encode()
    assert hashlib.sha256(encoded).hexdigest() == TABLE_SHA256
    assert len(DATA["classes"]) == 169
    assert len(DATA["tables"]) == 147
    for key, rows in DATA["tables"].items():
        assert len(rows) == 169, key
        for row in rows:
            assert len(row) == len(DATA["actions"])
            assert min(row) >= 0 and sum(row) == pytest.approx(1)


@pytest.mark.parametrize("depth", [5, 7.99, 8, 10, 12, 19.99, 20, 35, 50, 60, 80, 100, 200])
@pytest.mark.parametrize("context", ["BTN_OPEN", "BB_LIMP", "BB_OPEN", "BTN_ISOLATION", "VS_3BET", "VS_4BET", "VS_JAM"])
def test_full_grid_coverage_and_structural_sanity(depth, context):
    for hand in DATA["classes"]:
        p = lookup(context, depth, 2.5, hand).weights
        assert sum(p.values()) == pytest.approx(1)
        assert min(p.values()) > 0
        if context == "VS_JAM":
            assert p.keys() <= {"CALL", "FOLD"}
        if context == "BB_LIMP":
            assert "FOLD" not in p and "CALL" not in p
        if context == "BTN_OPEN" and depth >= 35:
            assert "ALL_IN" not in p
        if hand in {"AA", "KK"}:
            assert "FOLD" not in p


def test_price_response_and_depth_interpolation():
    for h in DATA["classes"]:
        small = lookup("BB_OPEN", 60, 2, h).weights
        large = lookup("BB_OPEN", 60, 5, h).weights
        assert large.get("FOLD", 0) >= small.get("FOLD", 0)-1e-9
    for h in DATA["classes"]:
        before = lookup("BTN_OPEN", 11.9999, 2, h).weights
        after = lookup("BTN_OPEN", 12.0001, 2, h).weights
        assert sum(abs(before.get(k, 0)-after.get(k, 0)) for k in before.keys() | after.keys()) < .001
    assert lookup("BTN_OPEN", 8, 2, "88").weights == {"ALL_IN": 1}
    assert lookup("BTN_OPEN", 100, 2, "88").weights.get("ALL_IN", 0) == 0


def test_large_open_and_deep_jam_are_not_minraise_defense():
    small = normalize(spot("Qh 8d", your_seat=1, action_history=BLINDS+[entry(0, "raise", 20)],
        pot=30, your_stack=990, opponent_stacks=[980], to_call=10))
    large = normalize(spot("Qh 8d", your_seat=1, action_history=BLINDS+[entry(0, "raise", 100)],
        pot=110, your_stack=990, opponent_stacks=[900], to_call=90))
    assert preflop_policy(large).weights.get("FOLD", 0) > preflop_policy(small).weights.get("FOLD", 0)
    jam = normalize(spot("Ah Qd", action_history=BLINDS+[entry(0, "raise", 25), entry(1, "raise", 1000)],
        pot=1025, your_stack=975, opponent_stacks=[0], to_call=975,
        valid_actions=["fold", "call"], min_raise=0, max_raise=0))
    assert preflop_policy(jam).weights == {"FOLD": 1}


def test_historical_depth_does_not_shrink_with_current_stacks():
    history = SRP+[entry(1, "raise", 300, "flop"), entry(0, "call", 300, "flop"),
                   entry(1, "raise", 600, "turn")]
    events = ledger(history, 10, {0: 675, 1: 75})
    assert events[2].effective_depth_bb == 100
    assert events[-1].effective_depth_bb == 67.5
    assert events[-1].pot_before == 650
    assert events[-1].size_ratio == pytest.approx(600/650)
    assert preflop_context(events[2], 0, 10) == ("BTN_OPEN", 2)
    assert preflop_context(events[3], 0, 10) == ("BB_OPEN", 2.5)


def test_call_price_and_ante_ledger():
    history = BLINDS+[entry(0, "post_ante", 2), entry(1, "post_ante", 2),
        entry(0, "raise", 25), entry(1, "call", 15),
        entry(1, "raise", 54, "flop"), entry(0, "call", 54, "flop")]
    last = ledger(history, 10, {0: 919, 1: 919})[-1]
    assert last.to_call == 54 and last.pot_before == 108
    assert last.size_ratio == 1 and last.call_odds == pytest.approx(1/3)
    assert ledger(history)[4].own_bet == 5  # antes never change the live bet
    history[5]["amount"] = 25  # old wire examples used call-to totals
    assert ledger(history)[-1] == ledger(history[:5]+[entry(1, "call", 15)]+history[6:])[-1]


def test_historical_effective_jam_detection():
    h = BLINDS+[entry(0, "raise", 100)]
    events = ledger(h, 10, {0: 900, 1: 90})
    assert events[-1].is_jam  # covering stack still puts the short opponent all-in
    h += [entry(1, "call", 90)]
    events = ledger(h, 10, {0: 900, 1: 0})
    assert events[-1].facing_jam


def test_range_uses_call_size_evidence():
    a = spot("Ah 7d", "As 8c 4d", action_history=SRP+[entry(0, "raise", 10, "flop"), entry(1, "call", 10, "flop")],
             pot=70, your_stack=965, opponent_stacks=[965])
    b = replace(a, action_history=SRP+[entry(0, "raise", 100, "flop"), entry(1, "call", 100, "flop")],
                pot=250, your_stack=875, opponent_stacks=[875])
    small = posterior(normalize(a), 1, perf_counter()+5)
    large = posterior(normalize(b), 1, perf_counter()+5)
    weak, strong = ("2c", "3d"), ("8d", "8h")
    assert large.weights[weak]/large.weights[strong] < small.weights[weak]/small.weights[strong]


def test_observed_large_open_conditions_a_stronger_range():
    small = normalize(spot("9h 7d", your_seat=1, action_history=BLINDS+[entry(0, "raise", 20)],
        pot=30, your_stack=990, opponent_stacks=[980], to_call=10))
    large = normalize(spot("9h 7d", your_seat=1, action_history=BLINDS+[entry(0, "raise", 100)],
        pot=110, your_stack=990, opponent_stacks=[900], to_call=90))
    a = posterior(small, 0, perf_counter()+5)
    b = posterior(large, 0, perf_counter()+5)
    assert b.weights[("2c", "3d")]/b.weights[("Ac", "Ad")] < a.weights[("2c", "3d")]/a.weights[("Ac", "Ad")]


def test_opponent_range_adjustment_is_gated_bounded_and_directional():
    model = OpponentModel()
    stat = model.stat("river_bet", .45)
    for _ in range(3):
        stat.observe(True)
    assert model.range_adjustment("river_bet") == 0
    for _ in range(197):
        stat.observe(True)
    assert 0 < model.range_adjustment("river_bet") <= .12
    raw = spot("Ah 7d", "As 8c 4d 2h 9s", pot=100, your_stack=975, opponent_stacks=[925],
               action_history=SRP+[entry(1, "raise", 50, "river")], to_call=50)
    plain = posterior(normalize(raw), 1, perf_counter()+5)
    wide = posterior(normalize(raw), 1, perf_counter()+5, opponent_model=model)
    weak, strong = ("2c", "3d"), ("8d", "8h")
    assert wide.weights[weak]/wide.weights[strong] > plain.weights[weak]/plain.weights[strong]


def test_timeout_does_not_supply_range_evidence():
    raw = spot("Ah 7d", "As 8c 4d", action_history=SRP)
    h = SRP+[dict(entry(1, "check", 0, "flop"), is_timeout=True)]
    a = posterior(normalize(raw), 1, perf_counter()+5)
    b = posterior(normalize(replace(raw, action_history=h)), 1, perf_counter()+5)
    assert a.weights == b.weights and a.last_update == b.last_update


def test_isolation_and_ip_probe_do_not_pollute_bb_open_or_donk():
    model = OpponentModel()
    h = BLINDS+[entry(0, "call", 5), entry(1, "raise", 35), entry(0, "fold")]
    model.observe_hand(1, {"action_history": h}, hero_seat=1, dealer_seat=0)
    assert "fold_vs_open" not in model.stats and model.stats["fold_to_isolation"].success == 1
    h = BLINDS+[entry(0, "raise", 25), entry(1, "raise", 90), entry(0, "call", 65),
        entry(1, "check", 0, "flop"), entry(0, "raise", 50, "flop")]
    model.observe_hand(2, {"action_history": h}, hero_seat=1, dealer_seat=0)
    assert "flop_donk" not in model.stats


def test_replay_with_opponent_adaptation():
    engine = Engine(Config(trace=False))
    for _ in range(90):
        engine.opponent.stat("river_bet", .45).observe(True)
    raw = spot("Ah 7d", "As 8c 4d 2h 9s", pot=100, your_stack=975, opponent_stacks=[925],
               action_history=SRP+[entry(1, "raise", 50, "river")], to_call=50)
    action = engine.evaluate(raw)
    trace = engine.last_trace
    from hu_bot import __version__
    assert trace["version"] == __version__ and trace["preflop_table_sha256"] == TABLE_SHA256
    replay = Engine(Config(trace=False))
    replay.opponent = engine.opponent
    assert replay.evaluate(raw, replay_trace=trace) == action
    assert replay.last_trace["equity"] == trace["equity"]


def test_v10_release_artifacts_are_unchanged():
    root = Path(__file__).resolve().parents[1]
    if not (root/"dist/hu-bot-v1.0.0.tar.gz").exists():
        pytest.skip("Optional local v1.0 artifact is not distributed inside source ZIP")
    with (root/"dist/hu-bot-v1.0.0.tar.gz").open("rb") as f:
        assert hashlib.file_digest(f, "sha256").hexdigest() == "0983d3ced58b8ce8ee9bd8775790fb72756920eb8c1ea76aed566c46e93876c4"
