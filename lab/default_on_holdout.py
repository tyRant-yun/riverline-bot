"""New-seed full-hand action EV for promoted anchors and depth windows."""
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

NODES = (("BTN_FIRST_IN", 50, 0), ("BTN_FIRST_IN", 60, 0),
         ("BTN_FIRST_IN", 65, 0), ("BTN_FIRST_IN", 70, 0),
         ("BB_VS_LIMP", 90, 1), ("BB_VS_LIMP", 100, 1),
         *(("BB_VS_OPEN", depth, 2) for depth in (50, 55, 60, 65, 70)),
         *(("BB_VS_OPEN", depth, 2.25) for depth in (55, 60, 65)))
SEEDS = (320101, 320102, 320103, 320104,
         320105, 320106, 320107, 320108)
SAMPLES_PER_SEED = 2


def audit(output):
    output = Path(output)
    if output.exists():
        raise FileExistsError(output)
    spec = _spec()
    spec_path = Path(__file__).resolve().parents[1]/"submission/hu_bot/range_spec_v183.json"
    panels = len(SEEDS)*SAMPLES_PER_SEED
    result = {"version": "1.8.3", "nodes": [], "rows": [],
        "seeds": SEEDS, "samples_per_seed": SAMPLES_PER_SEED,
        "spec_sha256": hashlib.sha256(spec_path.read_bytes()).hexdigest(),
        "independent_unit": "seed/sample full-hand world shared by old/new first actions",
        "profile_weights": PROFILE_WEIGHTS,
        "limitations": "Scripted future play and old-table arrival range; no matched HU solver or platform schedule."}
    for context, depth, price in NODES:
        sampler = arrival_sampler(context, depth, price)
        combo_weights = reach(context, depth, price)
        class_weights = defaultdict(float)
        for cards, weight in combo_weights.items():
            class_weights[hand_class(cards)] += weight
        actions = CONFIG["actions"][context]
        labels = spec["actions_by_context"][context]
        low, high, fraction = bracket(depth, (60, 100))
        low_table = spec["tables"][f"{context}:{low}:{price}"]
        high_table = spec["tables"][f"{context}:{high}:{price}"]
        gains = {profile: [0.0]*panels for profile in PROFILE_WEIGHTS}
        changed_classes = 0
        for index, klass in enumerate(DATA["classes"]):
            old = mapped_old(context, depth, price, klass)
            new = dict(zip(labels, ((1-fraction)*a+fraction*b for a, b in
                                    zip(low_table[index], high_table[index]))))
            delta = {action: new.get(action, 0)-old.get(action, 0) for action in actions}
            if all(abs(value) < 1e-10 for value in delta.values()):
                continue
            changed_classes += 1
            relevant = [action for action in actions if abs(delta[action]) > 1e-10]
            values = {profile: {action: [] for action in relevant}
                      for profile in PROFILE_WEIGHTS}
            for profile in PROFILE_WEIGHTS:
                for seed in SEEDS:
                    for sample in range(SAMPLES_PER_SEED):
                        identity = _seed("v183-confirmation", context, depth, price,
                                         klass, seed, sample)
                        deck = world(context, depth, price, klass, identity, sampler)
                        for action in relevant:
                            ev, _ = one_rollout(context, depth, price, action,
                                                deck, profile, identity)
                            values[profile][action].append(ev)
                for panel in range(panels):
                    gains[profile][panel] += class_weights[klass]*sum(
                        delta[action]*values[profile][action][panel]
                        for action in relevant)
            result["rows"].append({"context": context, "depth_bb": depth,
                "price_bb": price, "class": klass, "old": old, "new": new,
                "gain_by_profile_bb": {profile: statistics.mean([sum(
                    delta[action]*values[profile][action][panel]
                    for action in relevant) for panel in range(panels)])
                    for profile in PROFILE_WEIGHTS}})
        mixed = [sum(PROFILE_WEIGHTS[p]*gains[p][i] for p in PROFILE_WEIGHTS)
                 for i in range(panels)]
        mean = statistics.mean(mixed)
        se = statistics.stdev(mixed)/sqrt(panels)
        result["nodes"].append({"context": context, "depth_bb": depth,
            "price_bb": price, "changed_classes": changed_classes,
            "paired_gain_bb": mean, "paired_gain_se_bb": se,
            "paired_normal_95_ci_bb": [mean-1.96*se, mean+1.96*se],
            "profile_gain_bb": {p: statistics.mean(v) for p, v in gains.items()}})
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, separators=(",", ":"))+"\n", encoding="utf-8")
    return {"nodes": len(result["nodes"]), "changed_class_rows": len(result["rows"])}


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    print(json.dumps(audit(args.output)))
