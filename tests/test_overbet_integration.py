from dataclasses import replace
import json
from pathlib import Path

import pytest

from hu_bot.config import Config
from hu_bot.engine import Engine
from lab.scenarios import SRP, entry, spot


def facing(bet=200, hole="Ah 7d", board="As 8c 4d 2h 9s", **changes):
    values = dict(pot=50+bet, your_stack=975, opponent_stacks=[0],
        to_call=bet, min_raise=0, max_raise=0, valid_actions=["fold", "call"],
        action_history=SRP+[entry(1, "all_in", bet, "river")])
    values.update(changes)
    return spot(hole, board, **values)


def run(raw, **changes):
    engine = Engine(replace(Config(trace=False), **changes))
    action = engine.decide(raw)
    assert action.action in raw.valid_actions
    assert not engine.last_trace.get("fallback")
    return engine.last_trace


def test_new_models_default_off():
    assert not Config().polar_river_ranges and not Config().robust_river_defense
    trace = run(facing())
    assert trace["villain_range"]["river_components"] is None
    assert not trace["river_defense"]["applied"]


@pytest.mark.parametrize("bet", [75, 100, 200, 400])
def test_opt_in_supported_node_has_complete_components_and_call_fold_policy(bet):
    trace = run(facing(bet), polar_river_ranges=True, robust_river_defense=True)
    components = trace["villain_range"]["river_components"]
    assert components["ratio"] == bet/50 and not components["calibrated"]
    assert trace["river_defense"]["applied"]
    assert set(trace["policy"]) <= {"CALL", "FOLD"}
    assert sum(trace["policy"].values()) == pytest.approx(1)


def test_small_bet_is_outside_new_model():
    trace = run(facing(50), polar_river_ranges=True, robust_river_defense=True)
    assert trace["villain_range"]["river_components"] is None
    assert not trace["river_defense"]["applied"]


@pytest.mark.parametrize("bet", [75, 100, 200, 400])
def test_fixed_100bb_nonallin_first_overbet_is_supported(bet):
    raw = facing(bet, opponent_stacks=[975-bet], min_raise=2*bet, max_raise=975,
                 valid_actions=["fold", "call", "raise"],
                 action_history=SRP+[entry(1, "raise", bet, "river")])
    trace = run(raw, polar_river_ranges=True, robust_river_defense=True)
    assert trace["river_defense"]["applied"]
    assert set(trace["policy"]) <= {"CALL", "FOLD"}


def test_robust_flag_without_components_cannot_change_policy():
    original = run(facing())
    guarded = run(facing(), robust_river_defense=True)
    assert original["policy"] == guarded["policy"]
    assert not guarded["river_defense"]["applied"]


def test_public_royal_proof_survives_both_flags():
    trace = run(facing(hole="7h 2d", board="As Ks Qs Js Ts"),
                polar_river_ranges=True, robust_river_defense=True)
    assert trace["policy"] == {"CALL": 1.}


def test_compact_uploaded_trace_never_retains_components():
    trace = run(facing(), polar_river_ranges=True, robust_river_defense=True,
                retain_diagnostics=False)
    assert not trace["diagnostics_retained"]
    assert "river_defense" not in trace and "villain_range" not in trace
    assert len(json.dumps(trace)) < 1024


def test_default_policy_matches_frozen_v123_when_reference_available():
    frozen = Path(__file__).resolve().parents[2]/"hu-theory-v1.2.3"
    if not frozen.exists():
        pytest.skip("optional frozen v1.2.3 source reference not included")
    from lab.overbet_evaluation import frozen_factory
    reference = frozen_factory(frozen)()
    reference.decide(facing())
    assert run(facing())["policy"] == reference.last_trace["policy"]


def test_replay_retains_supported_defense_policy():
    raw = facing()
    engine = Engine(Config(trace=False, polar_river_ranges=True, robust_river_defense=True))
    engine.evaluate(raw)
    original = engine.last_trace
    engine.evaluate(raw, replay_trace=original)
    assert engine.last_trace["policy"] == original["policy"]
    assert engine.last_trace["action"] == original["action"]
