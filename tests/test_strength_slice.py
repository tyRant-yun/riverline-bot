from dataclasses import asdict, replace
import json

from hu_bot.accounting import DecisionSnapshot
from hu_bot.config import Config
from hu_bot.core import normalize
from hu_bot.engine import Engine
from hu_bot.ranges import EquityResult, RangeState
from hu_bot.strength_slice import assess_slice, build_node_key

from lab.scenarios import BLINDS, entry, spot


def _snapshot(raw):
    return DecisionSnapshot.from_game_state(raw)


def _assess(raw):
    state = normalize(raw, 10)
    empty = RangeState(state.node.id, {}, 0, 0)
    return assess_slice(state, _snapshot(raw), empty, empty,
                        EquityResult(.7, 0, .1, False, False, "test"))


def _river_history(*events):
    return (BLINDS + [entry(0, "raise", 25), entry(1, "call", 15),
                      entry(0, "check", 0, "flop"), entry(1, "check", 0, "flop"),
                      entry(0, "check", 0, "turn"), entry(1, "check", 0, "turn")]
            + list(events))


def _river_raw(history, *, hero_seat=0, board="As 8c 4d 2h 9s", pot=200,
               to_call=150):
    return spot("Ah 7d", board, pot=pot, your_stack=825,
                opponent_stacks=[875], your_seat=hero_seat, to_call=to_call,
                min_raise=300, max_raise=875,
                valid_actions=["fold", "call", "raise"], action_history=history)


def test_node_key_contains_explicit_rules_prices_sources_and_stable_json():
    raw = spot("Ah Ad", pot=19, action_history=BLINDS +
               [entry(0, "post_ante", 2), entry(1, "post_ante", 2)])
    state = normalize(raw, 10)
    snapshot = _snapshot(raw)
    key = build_node_key(state, snapshot)
    assert key.ante_by_seat == (2, 2)
    assert key.min_raise_to == state.min_raise
    assert key.max_raise_to == state.max_raise
    assert "raise_to:20:1000" in key.legal_actions
    assert key.data_sources
    assert json.dumps(asdict(key), sort_keys=True) == json.dumps(asdict(key), sort_keys=True)

    price_changed = build_node_key(replace(state, min_raise=30), snapshot)
    stack_changed = build_node_key(state, replace(snapshot, hand_start_stacks=(1000, 1000)))
    assert price_changed.digest != key.digest
    assert stack_changed.digest != key.digest


def test_action_value_keeps_probability_separate_and_does_not_fabricate_bb_ev():
    result = _assess(spot("Ah Ad"))
    evidence = result["action_value"]
    assert "values_bb" not in evidence and "uncertainty_bb" not in evidence
    assert 0 <= evidence["equity_probability"] <= 1
    assert evidence["equity_uncertainty_probability"] == .1
    assert evidence["action_ev_bb"] is None
    assert evidence["action_ev_status"] == "model_limited"
    assert result["strategy_gate"]["passed"] is False


def test_low_bluff_prior_is_current_street_first_opponent_large_bet_only():
    valid = _assess(_river_raw(_river_history(entry(1, "raise", 150, "river"))))
    assert valid["low_bluff_prior_scope"]["eligible"] is True
    assert valid["low_bluff_prior_scope"]["applied_to_strategy"] is False

    hero_bet_first = _assess(_river_raw(_river_history(
        entry(0, "raise", 150, "river"), entry(1, "raise", 150, "river"))))
    assert hero_bet_first["low_bluff_prior_scope"]["eligible"] is False
    assert hero_bet_first["low_bluff_prior_scope"]["reason"] == "first_active_was_hero"

    check_raise = _assess(_river_raw(_river_history(
        entry(1, "check", 0, "river"), entry(1, "raise", 150, "river"))))
    assert check_raise["low_bluff_prior_scope"]["eligible"] is False
    assert check_raise["low_bluff_prior_scope"]["reason"] == "opponent_check_raise"

    small_flop = _assess(_river_raw(_river_history(entry(1, "raise", 75, "flop")),
                                    board="As 8c 4d", pot=100, to_call=75))
    assert small_flop["low_bluff_prior_scope"]["eligible"] is False

    reversed_seat = _assess(_river_raw(_river_history(entry(0, "raise", 150, "river")),
                                       hero_seat=1))
    assert reversed_seat["low_bluff_prior_scope"]["eligible"] is True


def test_diagnostics_and_strategy_request_are_separate_and_default_off():
    raw = spot("Ah Ad")
    default = Engine(Config(trace=False, retain_diagnostics=True, max_samples=16))
    default.decide(raw)
    assert default.last_trace["strength_slice"]["status"] == "diagnostics_disabled"

    diagnostic = Engine(Config(trace=False, retain_diagnostics=True, max_samples=16,
                               strength_slice_diagnostics=True))
    diagnostic.decide(raw)
    assert "action_value" in diagnostic.last_trace["strength_slice"]

    requested = Engine(Config(trace=False, retain_diagnostics=True, max_samples=16,
                              strength_slice_strategy_enabled=True))
    requested.decide(raw)
    gate = requested.last_trace["strength_slice"]["strategy_gate"]
    assert gate["requested"] is True and gate["enabled"] is False and gate["passed"] is False
    assert requested.last_trace["action"] == default.last_trace["action"]

