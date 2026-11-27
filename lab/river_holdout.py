"""Fresh-seed paired complete-hand stress on the screenshot river route."""

from collections import defaultdict
from pathlib import Path
import hashlib
import json
import random
import statistics

from lab.double_pair_river import BOARD, PROFILES, hero_combos, posterior, replay


SEEDS = (191201, 191202, 191203, 191204, 191205, 191206)
ARMS = (("check", "call"), ("small", "fold"),
        ("medium", "fold"), ("medium", "call"))


def choose(rows, seed):
    u = random.Random(seed).random()
    total = 0.
    for villain, _, weight in rows:
        total += weight
        if u < total:
            return villain
    return rows[-1][0]


def run(output):
    output = Path(output)
    if output.exists():
        raise FileExistsError(output)
    paired = []
    for hero in hero_combos(BOARD):
        for profile in PROFILES:
            rows = posterior(BOARD, hero, profile)
            for seed in SEEDS:
                identity = int.from_bytes(hashlib.sha256(
                    repr((hero, profile, seed)).encode()).digest()[:8], "big")
                villain = choose(rows, identity)
                results = {f"{arm}:{facing}": replay(hero, villain, profile,
                                                       arm, facing, identity)
                           for arm, facing in ARMS}
                prefixes = [row["turn_prefix"] for row in results.values()]
                assert all(prefix == prefixes[0] for prefix in prefixes)
                assert all(row["hero_river_first_state"]["pot"] == 7654
                           for row in results.values())
                baseline = results["check:call"]["hero_net"]
                paired.append({"hero": hero, "villain": villain,
                               "profile": profile, "seed": seed,
                               "net_by_arm": {key: row["hero_net"] for key, row in results.items()},
                               "response_by_arm": {key: row["river_response"] for key, row in results.items()},
                               "delta_vs_check": {key: row["hero_net"] - baseline
                                                  for key, row in results.items() if key != "check:call"},
                               "legal": True})
    summaries = {}
    for profile in PROFILES:
        selected = [row for row in paired if row["profile"] == profile]
        seed_panels = defaultdict(list)
        for row in selected:
            for key, delta in row["delta_vs_check"].items():
                seed_panels[(row["seed"], key)].append(delta / 100)
        arm_summary = {}
        for key in ("small:fold", "medium:fold", "medium:call"):
            panels = [statistics.mean(seed_panels[(seed, key)]) for seed in SEEDS]
            mean = statistics.mean(panels)
            se = statistics.stdev(panels) / len(panels) ** .5
            arm_summary[key] = {"mean_delta_bb": mean,
                                "normal_95_interval_bb": [mean - 1.96 * se, mean + 1.96 * se],
                                "seed_panels_bb": panels,
                                "paired_worlds": len(selected)}
        summaries[profile] = {"paired_hands": len(selected), "arms": arm_summary}
    receipt = {"version": "1.9.1", "board": BOARD, "seeds": SEEDS,
               "profile_names": PROFILES, "rules_source": "assumed_for_research",
               "summary": summaries, "pairs": paired,
               "limitations": "Same scripted legal prefix and same deck/response seed per arm. Villain combos sampled from hypothesized posterior; these are not platform or independent-agent samples. Check arm calls a half-pot bet; raise branches are effective-stack capped in local harness."}
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(receipt, separators=(",", ":")) + "\n", encoding="utf-8")
    return summaries


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    print(json.dumps(run(args.output)))
