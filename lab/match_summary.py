"""Compact paired match receipt summary with trajectory and terminal chip changes."""
import json
from math import sqrt
from pathlib import Path
import statistics


def build(paths, output):
    output = Path(output)
    if output.exists():
        raise FileExistsError(output)
    result = {"source": "lab.hu100_matches assumed-rules paired contests",
              "experiments": []}
    for path in paths:
        x = json.loads(Path(path).read_text(encoding="utf-8"))
        pairs = x["pairs"]
        chip_diffs = []
        trajectory_changes = 0
        for p in pairs:
            chip_diffs.append(sum(p["candidate"][seat]["final_stacks"][seat]
                                  -p["baseline"][seat]["final_stacks"][seat]
                                  for seat in (0, 1))/2)
            for seat in (0, 1):
                old = p["baseline"][seat]["trajectory"]
                new = p["candidate"][seat]["trajectory"]
                trajectory_changes += any(a["hero_chips"] != b["hero_chips"]
                                          for a, b in zip(old, new))
        chip_mean = statistics.mean(chip_diffs)
        chip_se = (statistics.stdev(chip_diffs)/sqrt(len(chip_diffs))
                   if len(chip_diffs) > 1 else None)
        result["experiments"].append({"receipt": str(path),
            "opponent": x["opponent"], "rules": x["rules"],
            "pairs": len(pairs), "matches": x["match_count"],
            "censored_matches": x["censored_matches"],
            "paired_win_score_difference": x["paired_score_difference_mean"],
            "paired_win_normal_95_ci": x["paired_normal_95_ci"],
            "win_probability_interpretable": x["win_probability_interpretable"],
            "paired_terminal_chip_difference": chip_mean,
            "terminal_chip_normal_95_ci": None if chip_se is None else
                [chip_mean-1.96*chip_se, chip_mean+1.96*chip_se],
            "candidate_node_lookups": sum(a["policy_interventions"]
                for p in pairs for a in p["candidate"]),
            "changed_trajectories": trajectory_changes,
            "fallbacks_both_arms": sum(a["fallbacks"] for p in pairs
                for arm in ("baseline", "candidate") for a in p[arm]),
            "max_decision_ms": max(a["max_decision_ms"] for p in pairs
                for arm in ("baseline", "candidate") for a in p[arm])})
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, indent=2)+"\n", encoding="utf-8")
    return {"experiments": len(result["experiments"])}


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("output", type=Path)
    parser.add_argument("receipts", nargs="+", type=Path)
    args = parser.parse_args()
    print(json.dumps(build(args.receipts, args.output)))
