"""Holdout losses by continuation style and class, without retuning the table."""
from collections import defaultdict
import csv
import json
from math import sqrt
from pathlib import Path
import statistics

from hu_bot.poker import hand_class
from hu_bot.preflop import DATA
from lab.build_range_spec import PROFILE_WEIGHTS, mapped_old
from lab.range_compare import _rows, reach
from lab.range_rollout import CONFIG


def build(holdout_path, spec_path, output, counterexamples):
    if Path(output).exists() or Path(counterexamples).exists():
        raise FileExistsError("Sensitivity report already exists")
    rows = _rows(holdout_path, "holdout")
    spec = json.loads(Path(spec_path).read_text(encoding="utf-8"))
    result = {"source": "independent holdout under named scripted continuations",
              "profiles": PROFILE_WEIGHTS, "nodes": []}
    examples = []
    n = len(CONFIG["holdout_seeds"])*CONFIG["samples_per_seed"]
    for context, prices in CONFIG["scenarios"].items():
        for depth in CONFIG["depths_bb"]:
            for price in prices:
                class_weights = defaultdict(float)
                for cards, weight in reach(context, depth, price).items():
                    class_weights[hand_class(cards)] += weight
                actions = CONFIG["actions"][context]
                table = spec["tables"][f"{context}:{depth}:{price}"]
                panels = {profile: [0.0]*n for profile in PROFILE_WEIGHTS}
                for index, klass in enumerate(DATA["classes"]):
                    old = mapped_old(context, depth, price, klass)
                    new = dict(zip(actions, table[index]))
                    for profile in PROFILE_WEIGHTS:
                        diffs = [sum((new.get(action, 0)-old.get(action, 0))*
                            rows[(context, depth, price, klass, profile, action)]["values_bb"][i]
                            for action in actions) for i in range(n)]
                        for i, diff in enumerate(diffs):
                            panels[profile][i] += class_weights[klass]*diff
                        mean = statistics.mean(diffs)
                        se = statistics.stdev(diffs)/sqrt(n)
                        if mean < -0.05:
                            examples.append({"context": context, "depth_bb": depth,
                                "price_bb": price, "class": klass, "profile": profile,
                                "conditional_class_weight": class_weights[klass],
                                "paired_gain_bb": mean, "gain_se_bb": se,
                                "old": json.dumps(old, sort_keys=True),
                                "candidate": json.dumps(new, sort_keys=True)})
                node = {"context": context, "depth_bb": depth,
                        "price_bb": price, "profiles": {}}
                for profile, values in panels.items():
                    mean = statistics.mean(values)
                    se = statistics.stdev(values)/sqrt(n)
                    node["profiles"][profile] = {"paired_gain_bb": mean,
                        "paired_gain_se_bb": se,
                        "paired_normal_95_ci_bb": [mean-1.96*se, mean+1.96*se]}
                result["nodes"].append(node)
    Path(output).parent.mkdir(parents=True, exist_ok=True)
    Path(output).write_text(json.dumps(result, indent=2)+"\n", encoding="utf-8")
    examples.sort(key=lambda item: item["paired_gain_bb"])
    with Path(counterexamples).open("x", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(examples[0]) if examples else [])
        if examples:
            writer.writeheader()
            writer.writerows(examples)
    return {"profile_nodes": len(result["nodes"])*len(PROFILE_WEIGHTS),
            "class_profile_losses_below_0_05bb": len(examples)}


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    for name in ("holdout", "spec", "output", "counterexamples"):
        parser.add_argument(name, type=Path)
    args = parser.parse_args()
    print(json.dumps(build(args.holdout, args.spec,
                           args.output, args.counterexamples)))
