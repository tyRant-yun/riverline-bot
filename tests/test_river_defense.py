from types import SimpleNamespace
from time import perf_counter

import pytest

from hu_bot.core import Policy
from hu_bot.defense import defend_river_overbet


HERO = ("Ah", "Kd")
BOARD = ("2c", "7d", "9h", "Js", "Qc")
VALUE = ("Qd", "Qh")       # Hero loses to trips.
BLUFF = ("3c", "4d")       # Hero wins with ace high.


def state(**changes):
    values = dict(street="river", board=BOARD, hero_cards=HERO, hero_seat=0, num_players=2,
                  pot=250, to_call=150, hero_stack=500, hero_bet=0, villain_bet=150,
                  valid_actions=("fold", "call", "raise"),
                  action_history=({"seat": 0, "action": "check", "amount": 0, "phase": "river"},
                                  {"seat": 1, "action": "raise", "amount": 150, "phase": "river"}))
    values.update(changes)
    return SimpleNamespace(**values)


def villain(lo=.2, hi=.6, share=.4, **changes):
    components = dict(value_weights={VALUE: 1.0}, bluff_weights={BLUFF: 1.0},
                      bluff_share=share, bluff_interval=[lo, hi], ratio=1.5,
                      model="declared-heuristic-components", calibrated=False)
    components.update(changes.pop("component_changes", {}))
    return SimpleNamespace(complete=True, weights={VALUE: .5, BLUFF: .5},
                           river_components=components, **changes)


def equity(nuts=False):
    return SimpleNamespace(nuts=nuts, exact=True)


def stress_metadata():
    return {"central": {"percentile_cut": .70, "slope": 12},
            "wide": {"percentile_cut": .60, "slope": 9},
            "tight": {"percentile_cut": .80, "slope": 16}}


def stressed_villain(lo=.2, hi=.6, share=.4, **changes):
    component_changes = {"value_stress_weights": {"wide": {BLUFF: 1.0}, "tight": {VALUE: 1.0}},
                         "value_concentration": stress_metadata()}
    component_changes.update(changes.pop("component_changes", {}))
    return villain(lo, hi, share, component_changes=component_changes, **changes)


def decide(*, s=None, vr=None, base=None, eq=None, deadline=None, enabled=True, mix=.35):
    return defend_river_overbet(s or state(), base or Policy({"FOLD": 1}), vr or villain(),
                                eq or equity(), perf_counter()+1 if deadline is None else deadline,
                                enabled=enabled, mix_weight=mix)


def call_probability(policy):
    return None if policy is None else policy.weights.get("CALL", 0.0)


def test_value_component_interval_folds():
    policy, detail = decide(vr=villain(0, .2, .1))
    assert call_probability(policy) == 0 and detail["ev_interval"][1] < 0


def test_bluff_component_interval_calls():
    policy, detail = decide(vr=villain(.8, 1, .9))
    assert detail["target_call_probability"] == 1 and call_probability(policy) == pytest.approx(.35)
    assert detail["ev_interval"][0] > 0


def test_balanced_zero_endpoint_keeps_base_unchanged():
    policy, detail = decide(vr=villain(.375, .375, .375))
    assert policy is None and detail["reason"] == "all_component_endpoint_evs_zero_keep_base"


def test_nonsymmetric_crossing_uses_analytic_minimax_probability():
    policy, detail = decide(vr=villain(.2, .6, .4), base=Policy({"FOLD": .8, "CALL": .1, "RAISE_SMALL": .1}))
    # EV endpoints are -70 and +90; target call = 90/(90-(-70)) = .5625.
    assert detail["target_call_probability"] == pytest.approx(.5625)
    assert detail["base_call_probability"] == pytest.approx(.2)
    assert call_probability(policy) == pytest.approx(.65*.2 + .35*.5625)


def test_component_blocked_by_hero_cards_is_rejected():
    policy, detail = decide(vr=villain(component_changes={"bluff_weights": {HERO: 1.0}}))
    assert policy is None and detail["reason"] == "bluff_weights_invalid_combo_or_weight"


def test_component_outside_complete_range_is_rejected():
    outsider = ("5c", "6d")
    policy, detail = decide(vr=villain(component_changes={"bluff_weights": {outsider: 1.0}}))
    assert policy is None and detail["reason"] == "bluff_weights_outside_complete_range"


def test_nonfinite_bluff_share_is_rejected():
    policy, detail = decide(vr=villain(component_changes={"bluff_share": float("nan")}))
    assert policy is None and detail["reason"] == "river_components_invalid_bluff_share"


def test_non_normalized_component_is_rejected():
    policy, detail = decide(vr=villain(component_changes={"value_weights": {VALUE: .8}}))
    assert policy is None and detail["reason"] == "value_weights_not_normalized"


def test_nuts_preserves_existing_policy_before_component_use():
    policy, detail = decide(eq=equity(nuts=True), vr=SimpleNamespace())
    assert policy is None and detail["reason"] == "nuts_preserve_base_policy"


def test_incomplete_river_equity_cannot_suppress_a_possible_nuts_raise():
    policy, detail = decide(eq=SimpleNamespace(nuts=False, exact=False))
    assert policy is None and detail["reason"] == "exact_river_equity_required_to_check_nuts"


def test_short_call_truncation_is_rejected():
    policy, detail = decide(s=state(hero_stack=100))
    assert policy is None and detail["reason"] == "short_call_truncation_unsupported"


def test_flag_off_returns_no_override():
    policy, detail = decide(enabled=False)
    assert policy is None and detail["reason"] == "river_overbet_defense_disabled"


def test_timeout_is_atomic_no_override():
    policy, detail = decide(deadline=perf_counter()-1)
    assert policy is None and detail["reason"] == "deadline_atomic_discard"


def test_mix_weight_bounds_and_endpoints():
    base = Policy({"FOLD": 1})
    assert call_probability(decide(base=base, mix=0)[0]) == 0
    assert call_probability(decide(base=base, mix=1)[0]) == pytest.approx(.5625)
    policy, detail = decide(base=base, mix=1.1)
    assert policy is None and detail["reason"] == "invalid_mix_weight"


def test_value_shape_extrema_expand_minimax_endpoints_asymmetrically():
    policy, detail = decide(vr=stressed_villain(.2, .6, .4), mix=1)
    # Central/tight give [-70,+90], while the deliberately wider value shape
    # wins every showdown and raises the global maximum to +250.
    assert detail["value_shape_equity"] == {"central": 0.0, "wide": 1.0, "tight": 0.0}
    assert detail["value_shape_endpoint_evs"]["wide"] == {"lo": 250.0, "hi": 250.0}
    assert detail["ev_interval"] == [-70.0, 250.0]
    assert detail["target_call_probability"] == pytest.approx(250/320)
    assert call_probability(policy) == pytest.approx(250/320)


def test_malformed_provided_value_stress_map_is_rejected():
    policy, detail = decide(vr=stressed_villain(component_changes={"value_stress_weights": {"wide": {VALUE: 1.0}}}))
    assert policy is None and detail["reason"] == "value_stress_weights_invalid_shapes"


def test_absent_value_stress_remains_compatible_with_explicit_component_oracle():
    policy, detail = decide(vr=villain(.2, .6, .4))
    assert policy is not None and detail["value_shape_equity"] == {"central": 0.0}
    assert detail["value_concentration"] is None


def test_value_stress_timeout_is_atomic():
    policy, detail = decide(vr=stressed_villain(), deadline=perf_counter()-1)
    assert policy is None and detail["reason"] == "deadline_atomic_discard"
