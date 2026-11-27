"""Independent 65BB interpolation check using frozen candidate and new worlds."""
from collections import defaultdict
import json
from math import sqrt
from pathlib import Path
import statistics

from hu_bot.poker import hand_class
from hu_bot.preflop import DATA
from lab.build_range_spec import PROFILE_WEIGHTS, mapped_old
from lab.range_compare import reach
from lab.range_rollout import (CONFIG, _seed, arrival_sampler,
                               one_rollout, world)


DEPTH = 65
SEEDS = [200001, 200002]
SAMPLES_PER_SEED = 4


def audit(spec_path, output):
    output = Path(output)
    if output.exists():
        raise FileExistsError(output)
    spec = json.loads(Path(spec_path).read_text(encoding="utf-8"))
    n = len(SEEDS)*SAMPLES_PER_SEED
    receipt = {"depth_bb": DEPTH, "seeds": SEEDS,
               "samples_per_seed": SAMPLES_PER_SEED, "profile_weights": PROFILE_WEIGHTS,
               "model": "scripted full hand; independent 65BB worlds",
               "limitations": "No matched solver; short interpolation probe with 8 worlds per class/profile",
               "nodes": [], "rows": []}
    for context, prices in CONFIG["scenarios"].items():
        for price in prices:
            sampler = arrival_sampler(context, DEPTH, price)
            combo_weights = reach(context, DEPTH, price)
            class_weights = defaultdict(float)
            for cards, weight in combo_weights.items():
                class_weights[hand_class(cards)] += weight
            actions = CONFIG["actions"][context]
            labels = spec["actions_by_context"][context]
            i60, i100 = CONFIG["depths_bb"].index(60), CONFIG["depths_bb"].index(100)
            depths = CONFIG["depths_bb"]
            lo_table = spec["tables"][f"{context}:{depths[i60]}:{price}"]
            hi_table = spec["tables"][f"{context}:{depths[i100]}:{price}"]
            fraction = (DEPTH-60)/(100-60)
            panels = [0.0]*n
            for klass_index, klass in enumerate(DATA["classes"]):
                old = mapped_old(context, DEPTH, price, klass)
                new = {label: (1-fraction)*lo_table[klass_index][j]
                       +fraction*hi_table[klass_index][j]
                       for j, label in enumerate(labels)}
                values = {action: {profile: [] for profile in PROFILE_WEIGHTS}
                          for action in actions}
                for profile in PROFILE_WEIGHTS:
                    for seed in SEEDS:
                        for sample in range(SAMPLES_PER_SEED):
                            identity = _seed("65bb", context, price, klass, seed, sample)
                            deck = world(context, DEPTH, price, klass, identity, sampler)
                            for action in actions:
                                ev, _ = one_rollout(context, DEPTH, price, action,
                                                    deck, profile, identity)
                                values[action][profile].append(ev)
                action_ev = {action: sum(PROFILE_WEIGHTS[p]*statistics.mean(values[action][p])
                                         for p in PROFILE_WEIGHTS) for action in actions}
                gain = sum((new.get(action, 0)-old.get(action, 0))*action_ev[action]
                           for action in actions)
                receipt["rows"].append({"context": context, "price_bb": price,
                    "class": klass, "n_per_profile": n, "old": old, "candidate": new,
                    "action_ev_bb": action_ev, "gain_bb": gain,
                    "raw_values_bb": values})
                for panel in range(n):
                    panels[panel] += class_weights[klass]*sum(
                        PROFILE_WEIGHTS[profile]*(new.get(action, 0)-old.get(action, 0))*
                        values[action][profile][panel]
                        for profile in PROFILE_WEIGHTS for action in actions)
            mean = statistics.mean(panels)
            se = statistics.stdev(panels)/sqrt(n)
            receipt["nodes"].append({"context": context, "price_bb": price,
                "paired_gain_bb": mean, "paired_gain_se_bb": se,
                "paired_normal_95_ci_bb": [mean-1.96*se, mean+1.96*se]})
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(receipt, separators=(",", ":"))+"\n", encoding="utf-8")
    return {"nodes": len(receipt["nodes"]), "classes": len(receipt["rows"])}


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("spec", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    print(json.dumps(audit(args.spec, args.output)))
