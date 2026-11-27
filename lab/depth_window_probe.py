"""Exploratory action EV for adjacent depths before widening runtime gates."""
from collections import defaultdict
import hashlib
import json
from math import sqrt
from pathlib import Path
import statistics

from hu_bot.poker import hand_class
from hu_bot.preflop import DATA, bracket
from hu_bot.range_spec import _spec
from lab.build_range_spec import PROFILE_WEIGHTS, mapped_old
from lab.range_compare import reach
from lab.range_rollout import CONFIG, _seed, arrival_sampler, one_rollout, world

NODES = (("BTN_FIRST_IN", 50, 0), ("BTN_FIRST_IN", 65, 0),
         ("BTN_FIRST_IN", 70, 0),
         ("BB_VS_LIMP", 80, 1), ("BB_VS_LIMP", 90, 1),
         *(("BB_VS_OPEN", depth, price)
           for depth in (50, 55, 65, 70, 75, 80)
           for price in (2, 2.25)))
SEEDS = (310101, 310102, 310103, 310104)
SAMPLES_PER_SEED = 2


def new_policy(spec, context, depth, price, index):
    lo, hi, t = bracket(depth, (60, 100))
    labels = spec["actions_by_context"][context]
    left = spec["tables"][f"{context}:{lo}:{price}"][index]
    right = spec["tables"][f"{context}:{hi}:{price}"][index]
    return dict(zip(labels, ((1-t)*a+t*b for a, b in zip(left, right))))


def audit(output):
    output = Path(output)
    if output.exists():
        raise FileExistsError(output)
    spec = _spec()
    spec_path = Path(__file__).resolve().parents[1]/"submission/hu_bot/range_spec_v182.json"
    panels = len(SEEDS)*SAMPLES_PER_SEED
    result = {"source": "exploratory adjacent-depth full-hand scripted action EV",
        "seeds": SEEDS, "samples_per_seed": SAMPLES_PER_SEED,
        "spec_sha256": hashlib.sha256(spec_path.read_bytes()).hexdigest(),
        "limitations": "Table clamped below 60BB; fixed scripted continuation, not solver EV.",
        "nodes": []}
    for context, depth, price in NODES:
        sampler = arrival_sampler(context, depth, price)
        combo_weights = reach(context, depth, price)
        class_weights = defaultdict(float)
        for cards, weight in combo_weights.items():
            class_weights[hand_class(cards)] += weight
        actions = CONFIG["actions"][context]
        gains = {profile: [0.0]*panels for profile in PROFILE_WEIGHTS}
        changed_classes = 0
        for index, klass in enumerate(DATA["classes"]):
            old = mapped_old(context, depth, price, klass)
            new = new_policy(spec, context, depth, price, index)
            delta = {a: new.get(a, 0)-old.get(a, 0) for a in actions}
            relevant = [a for a, value in delta.items() if abs(value) > 1e-10]
            if not relevant:
                continue
            changed_classes += 1
            for profile in PROFILE_WEIGHTS:
                for seed in SEEDS:
                    for sample in range(SAMPLES_PER_SEED):
                        panel = SEEDS.index(seed)*SAMPLES_PER_SEED+sample
                        identity = _seed("depth-window-v182", context, depth,
                                         price, klass, seed, sample)
                        deck = world(context, depth, price, klass, identity, sampler)
                        delta_ev = sum(delta[action]*one_rollout(
                            context, depth, price, action, deck, profile, identity)[0]
                            for action in relevant)
                        gains[profile][panel] += class_weights[klass]*delta_ev
        mixed = [sum(PROFILE_WEIGHTS[p]*gains[p][i] for p in PROFILE_WEIGHTS)
                 for i in range(panels)]
        mean = statistics.mean(mixed)
        se = statistics.stdev(mixed)/sqrt(panels)
        result["nodes"].append({"context": context, "depth_bb": depth,
            "price_bb": price, "changed_classes": changed_classes,
            "paired_gain_bb": mean, "paired_95_ci_bb": [mean-1.96*se, mean+1.96*se],
            "profile_gain_bb": {p: statistics.mean(v) for p, v in gains.items()}})
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, separators=(",", ":"))+"\n", encoding="utf-8")
    return {"nodes": len(result["nodes"])}


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    print(json.dumps(audit(args.output)))
