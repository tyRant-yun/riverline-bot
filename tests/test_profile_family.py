from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).parents[1] / "submission"))

from chipzen import GameState
from chipzen.models import Card

from lab.profile_family import ARCHETYPES, ProfileFamily, parameter_manifest
from lab.conditional_eval import HOLDOUT_SEEDS, TRAIN_SEEDS, _cluster_interval, _run_unit, protocol
from lab.profile_calibration import calibrate


def state(**kwargs):
    values = dict(hand_number=1, phase="river",
                  hole_cards=[Card.from_str("As"), Card.from_str("Kd")],
                  board=[Card.from_str(x) for x in ("2c", "7d", "9h", "Js", "Qc")],
                  pot=100, your_stack=900, opponent_stacks=[900], your_seat=0,
                  dealer_seat=0, to_call=0, min_raise=100, max_raise=900,
                  valid_actions=["check", "raise"], action_history=[])
    values.update(kwargs)
    return GameState(**values)


def test_manifest_has_six_subtle_archetypes_and_controls():
    assert len(ARCHETYPES) == 8
    assert {"neutral", "extreme_control"} <= ARCHETYPES.keys()
    assert set(parameter_manifest()) == set(ARCHETYPES)
    assert all(0 <= p.raise_probability <= 1 for p in ARCHETYPES.values())


def test_actions_are_legal_and_raise_is_clamped():
    for name in ARCHETYPES:
        action = ProfileFamily(name, 10).decide(state())
        assert action.action in {"check", "raise"}
        if action.action == "raise":
            assert 100 <= action.amount <= 900


def test_calling_state_only_returns_legal_call_or_fold_or_raise():
    for name in ARCHETYPES:
        action = ProfileFamily(name, 10).decide(state(to_call=30, valid_actions=["fold", "call", "raise"]))
        assert action.action in {"fold", "call", "raise"}


def test_fixed_style_diagnostics_keep_opportunities_separate_from_reveals():
    result = _run_unit({"unit_id": "smoke-neutral", "split": "train",
                        "opponent": "neutral", "seed": 140001, "hands": 1,
                        "modes": ["baseline", "candidate_off", "raise_guard", "value_only", "both"],
                        "seats": [0, 1]})
    for mode in ("baseline", "candidate_off", "raise_guard", "value_only", "both"):
        diag = result["results"][mode]
        assert diag["river_first_action_opportunities"] >= diag["river_overbet_opportunities"]
        assert diag["river_selected_reveals"] >= 0


def test_protocol_split_and_cluster_uncertainty_are_predeclared():
    spec = protocol(("neutral",), hands=150)
    assert [u["seed"] for u in spec["units"] if u["split"] == "train"] == list(TRAIN_SEEDS)
    assert [u["seed"] for u in spec["units"] if u["split"] == "holdout"] == list(HOLDOUT_SEEDS)
    interval = _cluster_interval([1.0, 3.0, 2.0])
    assert interval["units"] == 3 and interval["t95_ci"][0] < interval["mean"] < interval["t95_ci"][1]


def test_profiles_have_distinct_realized_biases_on_shared_public_states():
    result = calibrate(deals=1000, seed=7411)
    spots = result["spots"]
    def fold(spot, name):
        return spots[spot]["profiles"][name]["rates"].get("fold", 0)
    assert fold("pot", "sticky_caller") < fold("pot", "neutral")
    assert fold("pot", "neutral") < fold("pot", "mild_overfolder")
    assert fold("pot", "mild_overfolder") < fold("pot", "extreme_control")
    assert (fold("third_pot", "size_threshold_folder") <
            fold("pot", "size_threshold_folder") <
            fold("overbet", "size_threshold_folder"))
    opened = spots["unopened"]["profiles"]
    assert (opened["value_river_bettor"]["actions"]["raise"] >
            opened["neutral"]["actions"]["raise"])
    assert (opened["bluff_leaning_river_bettor"]["weak_bets"] >
            opened["value_river_bettor"]["weak_bets"])
