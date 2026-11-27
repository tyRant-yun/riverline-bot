"""Uniform 1326-combo and blocker-conditioned old/new action frequencies."""
from collections import defaultdict
import csv
import gzip
import json
from pathlib import Path


def build(diff_path, train_path, output):
    output = Path(output)
    if output.exists():
        raise FileExistsError(output)
    receipt = json.loads(Path(train_path).read_text(encoding="utf-8"))
    actual = {}
    for row in receipt["rows"]:
        key = (row["context"], row["depth_bb"], row["price_bb"], row["action"])
        actual.setdefault(key, row["executed_example"])
    grouped = defaultdict(lambda: {arm: {weighting: defaultdict(float)
                                         for weighting in ("uniform_1326", "conditional")}
                                  for arm in ("old", "candidate")})
    counts = defaultdict(int)
    with gzip.open(diff_path, "rt", encoding="utf-8", newline="") as stream:
        for row in csv.DictReader(stream):
            context, depth, price = row["context"], int(row["depth_bb"]), float(row["price_bb"])
            key = (context, depth, price)
            counts[key] += 1
            for arm, column in (("old", "old_policy"), ("candidate", "candidate_policy")):
                for action, probability in json.loads(row[column]).items():
                    executable = actual[(context, depth, price, action)]
                    label = (f"{executable['action']}:{executable['amount']}"
                             if executable["action"] == "raise" else executable["action"])
                    grouped[key][arm]["uniform_1326"][label] += probability/1326
                    grouped[key][arm]["conditional"][label] += (
                        probability*float(row["conditional_weight"]))
    result = {"range_diff": str(diff_path), "reach_model":
              "frozen old own-path and opponent arrival; exact card blockers",
              "pricing": "actual integer raise-to from full-hand rollout examples",
              "nodes": []}
    for key, values in grouped.items():
        if counts[key] != 1326:
            raise ValueError((key, counts[key]))
        context, depth, price = key
        node = {"context": context, "depth_bb": depth, "price_bb": price,
                "combos": counts[key], "frequencies": {}}
        for arm, weights in values.items():
            node["frequencies"][arm] = {name: dict(sorted(actions.items()))
                                         for name, actions in weights.items()}
            for frequencies in node["frequencies"][arm].values():
                if abs(sum(frequencies.values())-1) > 1e-7:
                    raise ValueError((key, arm, frequencies))
        result["nodes"].append(node)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, indent=2)+"\n", encoding="utf-8")
    return {"nodes": len(result["nodes"]), "combos_per_node": 1326}


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("diff", type=Path)
    parser.add_argument("train", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    print(json.dumps(build(args.diff, args.train, args.output)))
