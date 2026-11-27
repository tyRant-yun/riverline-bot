"""New-seed, class-wide conditional full-hand audit of unpromoted nodes."""
from collections import defaultdict
from pathlib import Path
import hashlib
import json
import statistics

from hu_bot.poker import hand_class
from hu_bot.preflop import DATA
from hu_bot.range_spec import _spec
from lab.build_range_spec import mapped_old
from lab.range_compare import reach
from lab.range_rollout import _seed, arrival_sampler, one_rollout, world

NODES = (("BTN_FIRST_IN", 100, 0),
         ("BB_VS_OPEN", 100, 2), ("BB_VS_OPEN", 100, 2.25),
         ("BB_VS_OPEN", 100, 2.5), ("BB_VS_LIMP", 60, 1),
         ("BB_VS_OPEN", 60, 2.5))
PROFILES = ("tight", "call", "aggressive")
SEEDS = (190101, 190102, 190103, 190104)


def run(output, seeds=SEEDS):
    output = Path(output)
    if output.exists():
        raise FileExistsError(output)
    spec = _spec()
    rows, nodes = [], []
    for context, depth, price in NODES:
        sampler = arrival_sampler(context, depth, price)
        class_reach = defaultdict(float)
        for cards, weight in reach(context, depth, price).items():
            class_reach[hand_class(cards)] += weight
        node_gain = {profile: [0.0] * len(seeds) for profile in PROFILES}
        changed_classes = 0
        labels = spec["actions_by_context"][context]
        table = spec["tables"][f"{context}:{depth}:{price}"]
        for index, klass in enumerate(DATA["classes"]):
            old = mapped_old(context, depth, price, klass)
            new = dict(zip(labels, table[index]))
            delta = {label: new.get(label, 0) - old.get(label, 0)
                     for label in labels}
            relevant = [label for label in labels if abs(delta[label]) > 1e-10]
            row = {"context": context, "depth_bb": depth, "price_bb": price,
                   "class": klass, "reach_weight": class_reach[klass],
                   "old": old, "research_candidate": new,
                   "policy_l1": sum(abs(value) for value in delta.values()),
                   "gain_by_profile_bb": {}}
            if relevant:
                changed_classes += 1
                for profile in PROFILES:
                    gains = []
                    for seed_index, seed in enumerate(seeds):
                        identity = _seed("v190-fresh", context, depth, price, klass, seed)
                        deck = world(context, depth, price, klass, identity, sampler)
                        values = {action: one_rollout(context, depth, price, action,
                                                     deck, profile, identity)[0]
                                  for action in relevant}
                        gain = sum(delta[action] * values[action] for action in relevant)
                        gains.append(gain)
                        node_gain[profile][seed_index] += class_reach[klass] * gain
                    row["gain_by_profile_bb"][profile] = statistics.mean(gains)
            rows.append(row)
        node = {"context": context, "depth_bb": depth, "price_bb": price,
                "changed_classes": changed_classes, "class_count": len(DATA["classes"]),
                "gain_by_profile_bb": {profile: statistics.mean(panels)
                                       for profile, panels in node_gain.items()},
                "seed_panels_by_profile_bb": node_gain,
                "decision": "research_only_keep_v184_default_until_stronger_validation"}
        nodes.append(node)
    receipt = {"version": "1.9.0", "nodes": nodes, "rows": rows,
               "seeds": seeds, "profiles": PROFILES,
               "table_sha256": hashlib.sha256(
                   Path(__file__).resolve().parents[1].joinpath(
                       "submission/hu_bot/range_spec_v183.json").read_bytes()).hexdigest(),
               "rules_source": "assumed_for_research",
               "limitation": "Forced prefix, scripted continuation, only four worlds per class/profile; not platform or solver EV."}
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(receipt, separators=(",", ":")) + "\n", encoding="utf-8")
    return {"nodes": len(nodes), "class_rows": len(rows),
            "changed_classes": sum(node["changed_classes"] for node in nodes)}


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("output", type=Path)
    parser.add_argument("--holdout", action="store_true")
    args = parser.parse_args()
    seeds = tuple(range(190201, 190217)) if args.holdout else SEEDS
    print(json.dumps(run(args.output, seeds)))
