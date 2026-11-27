"""Small preregistered river micro-games; no tournament-strength inference.

Truth and fitted predictions are separate. The independent oracle owns terminal
cash flows; production EV helpers are called only as systems under test.
"""
import argparse
from collections import Counter
import json
from pathlib import Path
import random
import sys
from time import perf_counter

from lab.checkpoints import _sha, _write_new, source_fingerprints
from lab.oracles import (PREDICTION_CLASSES, bet_fold_ev, brier_score, call_ev,
                         hand_checked_examples, indistinguishable_fcr_fixture,
                         log_loss, regret, weighted_ev)


def condition_combos(weights, known):
    """Remove impossible hole-card combinations and renormalize remaining mass."""
    from math import isfinite
    if any(not isfinite(w) or w < 0 or len(c) != 2 or len(set(c)) != 2
           for c, w in weights.items()):
        raise ValueError("invalid combo or weight")
    result = {c: w for c, w in weights.items() if not set(c).intersection(known) and w > 0}
    total = sum(result.values())
    if total <= 0:
        raise ValueError("no possible combinations")
    return {c: w/total for c, w in result.items()}


def draw_responses(probabilities, count, rng):
    return rng.choices(PREDICTION_CLASSES, [probabilities[k] for k in PREDICTION_CLASSES], k=count)


def action_values(response, called_win=.4):
    # Check equity .43 is fixed by the complete hidden-state fixture. The
    # conditional call range has equity .4, so they must not be interchanged.
    return {"check": 43., "bet": bet_fold_ev(100, 50, response["fold"],
            response["call"], response["raise"], called_win, 0., 1-called_win)}


def microgame(delta, count, seed, holdout_count=500):
    prior = {"fold": .3, "call": .6, "raise": .1}
    truth = {"fold": .3+delta, "call": .6-delta, "raise": .1}
    # Holding total check equity .43 fixed while shifting F/C is feasible:
    # folds' conditional win mass compensates calls, raises' hero equity=0.
    fold_equity = (.43-truth["call"]*.4)/truth["fold"]
    assert 0 <= fold_equity <= 1
    counts = Counter(draw_responses(truth, count, random.Random(seed)))
    predicted = {k: (20*prior[k]+counts[k])/(20+count) for k in prior}
    holdout = draw_responses(truth, holdout_count, random.Random(seed+1000003))
    baseline_action = max(action_values(prior), key=action_values(prior).get)
    fitted = action_values(predicted)
    selected = max(fitted, key=fitted.get)
    policy = {baseline_action: .8}
    policy[selected] = policy.get(selected, 0)+.2
    oracle = action_values(truth)
    baseline_regret = regret(oracle, {baseline_action: 1.})
    actual_regret = regret(oracle, policy)
    return {"delta_fold_pp": delta*100, "training_n": count, "holdout_n": holdout_count,
            "seed": seed, "truth": truth, "fold_conditional_hero_equity": fold_equity,
            "observed_counts": dict(counts), "predicted": predicted,
            "brier": sum(brier_score(predicted, a) for a in holdout)/holdout_count,
            "log_loss": sum(log_loss(predicted, a) for a in holdout)/holdout_count,
            "prior_brier": sum(brier_score(prior, a) for a in holdout)/holdout_count,
            "prior_log_loss": sum(log_loss(prior, a) for a in holdout)/holdout_count,
            "policy": policy, "true_action_evs": oracle,
            "true_policy_ev": weighted_ev(oracle, policy), "regret_chips": actual_regret,
            "baseline_regret_chips": baseline_regret,
            "wrong_adaptation": actual_regret > baseline_regret+1e-9}


def runtime_checks():
    runtime = str(Path(__file__).resolve().parents[1]/"submission")
    if runtime not in sys.path:
        sys.path.insert(0, runtime)
    from hu_bot.config import Config
    from hu_bot.core import normalize
    from hu_bot.engine import Engine
    from hu_bot.river import bet_fold_ev as production_bet, call_ev as production_call
    from hu_bot.strategy import legalize
    from lab.scenarios import scenarios
    expected = hand_checked_examples()
    assert expected["check_100"] == 55 and expected["call_25"] == 37.5
    assert expected["bet_fold"] == 67
    assert production_call(100, 25, .5) == call_ev(100, 25, .4, .2, .4)
    assert production_bet(100, 50, .3, .6, .1, .6) == expected["bet_fold"]
    assert production_bet(100, 50, 0, 1, 0, .5) == 50  # all-in tie: no raise
    assert production_bet(100, 50, 0, 0, 1, .6) == -50
    # Explicit mutations: double counting a call, dropping raise loss and using
    # full-range rather than conditional equity must not pass these fixtures.
    detected = {"double_count_call": .5*(100+2*25)-25 != expected["call_25"],
                "omit_raise_loss": .3*100+.6*(.6*200-50) != expected["bet_fold"],
                "wrong_conditional_equity": production_bet(100, 50, .3, .6, .1, .55) != 67}
    assert all(detected.values())
    state = scenarios()["river_thin_value"]
    engine = Engine(Config(trace=False))
    engine.reset("f03-visible-fixture")
    engine.decide(state)
    trace = engine.last_trace
    assert trace["river_ev"]["complete"], trace["river_ev"]
    assert trace["attribution"]["policy_L1_change"]["response_only"] is None
    restricted = scenarios()["river_thin_value"]
    restricted.valid_actions = ["check"]
    action, _ = legalize("BET_OVERBET", normalize(restricted))
    assert action.action == "check"
    return {"hand_checked": expected, "mutations_detected": detected,
            "production_river_complete": True, "attribution": trace["attribution"],
            "unavailable_raise_remapped": action.to_dict(),
            "trace_contains_hidden_truth": False,
            "note": "Engine receives only a public GameState, not oracle truth or training labels."}


def run(destination, seed=20260922):
    destination = Path(destination)
    cases = [{"delta": d, "n": n, "seed": seed+i*1009+j*17}
             for i, d in enumerate((-.10, -.05, 0., .05, .10))
             for j, n in enumerate((8, 200))]
    manifest = {"experiment_type": "F03-river-microgames-v1", "seed": seed,
                "cases": cases, "holdout_n_per_case": 500, "epsilon": 1e-12,
                "prior_strength": 20, "test_policy_mix": .2,
                "worker": source_fingerprints(),
                "limitations": "Synthetic calibration fixtures, not validation of learned Chipzen exploit or win rate."}
    fingerprint = _sha(manifest)
    # Freeze before evaluation; F02's atomic exclusive writer prevents accidental
    # replacement. This is not mislabelled as a cash session-pair checkpoint.
    _write_new(destination/"protocol.json", {**manifest, "fingerprint": fingerprint})
    start = perf_counter()
    results = [microgame(c["delta"], c["n"], c["seed"]) for c in cases]
    worlds = indistinguishable_fcr_fixture()
    hidden = [{"name": w.name, "response_marginals": w.response_marginals,
               "call_composition": w.call_composition,
               "bet_ev": bet_fold_ev(100, 50, .3, .6, .1, **{
                   "call_"+k: v for k, v in w.call_composition.items()})} for w in worlds]
    blockers = condition_combos({("Ah", "Kd"): .5, ("Ac", "Kh"): .3,
                                ("2d", "2c"): .2}, {"Ah", "As", "8c", "4d", "2h", "9s"})
    assert abs(blockers[("Ac", "Kh")]-.6) < 1e-12
    output = {"protocol_fingerprint": fingerprint, "cases": results,
              "hidden_worlds": hidden, "blocker_conditioned": {" ".join(c): p for c, p in blockers.items()},
              "runtime_checks": runtime_checks(), "elapsed_seconds": perf_counter()-start,
              "strength_validated": False}
    if source_fingerprints() != manifest["worker"]:
        raise RuntimeError("source changed during calibration")
    _write_new(destination/"results.json", {"payload": output, "payload_sha256": _sha(output)})
    return output


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    result = run(args.output)
    print(json.dumps({"cases": len(result["cases"]), "strength_validated": False,
                      "protocol_fingerprint": result["protocol_fingerprint"]}))
