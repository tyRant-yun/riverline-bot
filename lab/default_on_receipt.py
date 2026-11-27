"""Materialize the v1.8 promoted range subset from frozen v1.7 receipts."""
import csv
import gzip
import hashlib
import json
from pathlib import Path


def promoted(context, depth, price):
    return (context == "BTN_FIRST_IN" and depth == 60 or
            context == "BB_VS_LIMP" and depth == 100) or (
        context == "BB_VS_OPEN" and depth == 60 and 2 <= price <= 2.25)


def build(source_diff, source_holdout, output_diff, output_summary):
    paths = [Path(source_diff), Path(source_holdout), Path(output_diff), Path(output_summary)]
    if any(p.exists() for p in paths[2:]):
        raise FileExistsError("Refusing to overwrite audit receipts")
    for p in paths[2:]:
        p.parent.mkdir(parents=True, exist_ok=True)
    nodes = {}
    holdout = json.loads(paths[1].read_text(encoding="utf-8"))
    holdout_nodes = {(n["context"], n["depth_bb"], float(n["price_bb"])): n
                     for n in holdout["nodes"]}
    with gzip.open(paths[0], "rt", encoding="utf-8", newline="") as src, \
            gzip.open(paths[2], "wt", encoding="utf-8", newline="") as dst:
        reader = csv.DictReader(src)
        writer = csv.DictWriter(dst, fieldnames=["v180_enabled", *reader.fieldnames])
        writer.writeheader()
        for row in reader:
            key = row["context"], int(row["depth_bb"]), float(row["price_bb"])
            enabled = promoted(*key)
            nodes.setdefault(key, {"rows": 0, "changed_combos": 0, "enabled": enabled})
            nodes[key]["rows"] += 1
            if not enabled:
                row["candidate_policy"] = row["old_policy"]
                row["policy_l1"] = "0"
                row["holdout_candidate_ev_bb"] = row["holdout_old_ev_bb"]
                row["holdout_gain_bb"] = "0"
            else:
                nodes[key]["changed_combos"] += float(row["policy_l1"]) > 1e-9
            writer.writerow({"v180_enabled": int(enabled), **row})
    summary = {"version": "1.8.3", "source_diff_sha256": hashlib.sha256(
        paths[0].read_bytes()).hexdigest(), "source_holdout_sha256": hashlib.sha256(
            paths[1].read_bytes()).hexdigest(), "rules": holdout["rules"],
        "source": "frozen v1.7 rollout; v1.8 runtime applies only promoted contexts",
        "limitations": "Anchor EV is class-shared and scripted; no matched four-street solver.",
        "nodes": []}
    for key, details in sorted(nodes.items()):
        if details["rows"] != 1326:
            raise ValueError((key, details["rows"]))
        old = holdout_nodes[key]
        summary["nodes"].append({"context": key[0], "depth_bb": key[1],
            "price_bb": key[2], **details,
            "holdout_gain_bb": old["paired_gain_bb"] if details["enabled"] else 0,
            "holdout_95_ci_bb": old["paired_normal_95_ci_bb"] if details["enabled"] else None})
    paths[3].write_text(json.dumps(summary, indent=2)+"\n", encoding="utf-8")
    return {"nodes": len(nodes), "enabled_nodes": sum(n["enabled"] for n in nodes.values()),
            "rows": sum(n["rows"] for n in nodes.values())}


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    for name in ("source_diff", "source_holdout", "output_diff", "output_summary"):
        parser.add_argument(name, type=Path)
    args = parser.parse_args()
    print(json.dumps(build(args.source_diff, args.source_holdout,
                           args.output_diff, args.output_summary)))
