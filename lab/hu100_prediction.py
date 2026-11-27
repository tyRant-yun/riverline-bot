"""Predeclared synthetic next-opportunity log-loss; not match strength evidence."""
from collections import Counter
import json
from math import log
from pathlib import Path
import random

from hu_bot.preflop_blueprint import PreflopBlueprint
from hu_bot.preflop_observation import OpportunityLedger, PreflopNodeKey
from lab.scenarios import BLINDS, entry


CONFIG = {
    "version": 1,
    "node": "BB_VS_OPEN",
    "raise_to": 22,
    "big_blind": 10,
    "depth_bb": 100,
    "ante": 0,
    "dev_seeds": list(range(73100, 73120)),
    "holdout_seeds": list(range(83100, 83120)),
    "opportunities_per_seed": 60,
    "prior_strength_candidates": [5, 20, 50],
    "profiles": {
        "normal": [0.41, 0.53, 0.06],
        "overfold": [0.80, 0.17, 0.03],
        "reverse": [0.15, 0.70, 0.15],
        "switch_overfold_to_normal_after_20": [0.80, 0.17, 0.03],
    },
    "profile_order": ["fold", "call", "raise"],
    "limit": "Synthetic observable-action mechanism check only; no real opponents or hidden-card ranges.",
}


def _draw(rng, probabilities):
    sample = rng.random()
    for label, p in zip(CONFIG["profile_order"], probabilities):
        sample -= p
        if sample < 0:
            return label
    return "raise"


def _history(response):
    result = {"fold": entry(1, "fold"), "call": entry(1, "call", 12),
              "raise": entry(1, "raise", 73)}[response]
    history = BLINDS+[entry(0, "raise", 22), result]
    if response == "raise":
        history.append(entry(0, "fold"))
    return history


def score(seed, profile, weight, prior):
    rng = random.Random(seed)
    ledger = OpportunityLedger()
    key = PreflopNodeKey("BB_VS_OPEN", 22, 10, "100bb", 0)
    actual, baseline, adapted = Counter(), 0.0, 0.0
    for index in range(CONFIG["opportunities_per_seed"]):
        probability = (CONFIG["profiles"]["normal"] if
                       profile == "switch_overfold_to_normal_after_20" and index >= 20
                       else CONFIG["profiles"][profile])
        response = _draw(rng, probability)
        counts = ledger.counts.get(key, Counter())
        n = sum(counts.values())
        prediction = (weight*prior[response]+counts.get(response, 0))/(weight+n)
        baseline -= log(prior[response])
        adapted -= log(prediction)
        actual[response] += 1
        ledger.observe_hand((seed, profile, index), {"action_history": _history(response)},
                            hero_seat=0, dealer_seat=0, big_blind=10, depth_bb=100)
    assert ledger.counts[key] == actual
    return {"baseline_log_loss": baseline/60, "adapted_log_loss": adapted/60,
            "counts": dict(actual), "skips": dict(ledger.skipped)}


def run(output):
    output = Path(output)
    if output.exists():
        raise FileExistsError(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    prior = PreflopBlueprint().bb_response_prior(22, 10)
    dev = {}
    for weight in CONFIG["prior_strength_candidates"]:
        runs = [score(seed, profile, weight, prior)
                for profile in CONFIG["profiles"] for seed in CONFIG["dev_seeds"]]
        dev[weight] = sum(r["adapted_log_loss"] for r in runs)/len(runs)
    selected = min(dev, key=dev.get)
    holdout = {}
    for profile in CONFIG["profiles"]:
        runs = [score(seed, profile, selected, prior) for seed in CONFIG["holdout_seeds"]]
        holdout[profile] = {
            "seeds": CONFIG["holdout_seeds"], "opportunities": len(runs)*60,
            "baseline_mean_log_loss": sum(r["baseline_log_loss"] for r in runs)/len(runs),
            "adapted_mean_log_loss": sum(r["adapted_log_loss"] for r in runs)/len(runs),
            "actual_counts": dict(sum((Counter(r["counts"]) for r in runs), Counter())),
            "skipped": dict(sum((Counter(r["skips"]) for r in runs), Counter())),
        }
        holdout[profile]["improvement_nats"] = (
            holdout[profile]["baseline_mean_log_loss"]
            -holdout[profile]["adapted_mean_log_loss"])
    receipt = {"config": CONFIG, "heuristic_prior": prior,
               "dev_adapted_log_loss_by_weight": dev, "selected_prior_weight": selected,
               "holdout": holdout, "online_prior_weight": selected,
               "online_enable_recommendation": False}
    output.write_text(json.dumps(receipt, indent=2)+"\n", encoding="utf-8")
    return receipt


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    print(json.dumps(run(args.output)["holdout"], indent=2))
