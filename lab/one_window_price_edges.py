"""Stress the single 2.20-2.30BB window around the 2.25BB research anchor.

The frozen 2.25BB candidate weights are projected to each edge only for an
offline counterfactual. No edge is eligible in the production runtime.
"""

from collections import defaultdict
from pathlib import Path
import json
import statistics

from hu_bot.poker import hand_class
from hu_bot.preflop import DATA
from hu_bot.range_spec import _spec
from lab.build_range_spec import mapped_old
from lab.range_compare import reach
from lab.range_rollout import _seed, arrival_sampler, one_rollout, world


PRICES = (2.20, 2.30)
SEEDS = tuple(range(191321, 191329))
PROFILES = ("tight", "call", "aggressive")
CONTEXT, DEPTH = "BB_VS_OPEN", 100


def run(output):
    output = Path(output)
    if output.exists():
        raise FileExistsError(output)
    spec = _spec()
    labels = spec["actions_by_context"][CONTEXT]
    anchor = spec["tables"][f"{CONTEXT}:{DEPTH}:2.25"]
    receipts = []
    for price in PRICES:
        sampler = arrival_sampler(CONTEXT, DEPTH, price)
        reach_by_class = defaultdict(float)
        for cards, weight in reach(CONTEXT, DEPTH, price).items():
            reach_by_class[hand_class(cards)] += weight
        panels = {p: {seed: 0.0 for seed in SEEDS} for p in PROFILES}
        rows = []
        for index, klass in enumerate(DATA["classes"]):
            old = mapped_old(CONTEXT, DEPTH, price, klass)
            candidate = dict(zip(labels, anchor[index]))
            delta = {label: candidate.get(label, 0) - old.get(label, 0)
                     for label in labels}
            relevant = [label for label in labels if abs(delta[label]) > 1e-10]
            row = {"class": klass, "old": old,
                   "projected_research_anchor": candidate,
                   "policy_l1": sum(abs(x) for x in delta.values()),
                   "reach_weight": reach_by_class[klass],
                   "action_ev_by_profile_bb": {}}
            for profile in PROFILES:
                action_values = {label: [] for label in relevant}
                for seed in SEEDS:
                    if not relevant:
                        continue
                    identity = _seed("v191-price-edge", price, klass, profile, seed)
                    deck = world(CONTEXT, DEPTH, price, klass, identity, sampler)
                    values = {label: one_rollout(CONTEXT, DEPTH, price, label,
                                                 deck, profile, identity)[0]
                              for label in relevant}
                    for label, value in values.items():
                        action_values[label].append(value)
                    panels[profile][seed] += reach_by_class[klass] * sum(
                        delta[label] * values[label] for label in relevant)
                row["action_ev_by_profile_bb"][profile] = {
                    label: statistics.mean(values) for label, values in action_values.items()}
            rows.append(row)
        receipts.append({"price_bb": price, "class_count": len(rows),
                         "changed_class_count": sum(
                             row["policy_l1"] > 1e-10 for row in rows),
                         "gain_by_profile_bb": {p: statistics.mean(list(panel.values()))
                                                for p, panel in panels.items()},
                         "normal_95_interval_by_profile_bb": {
                             p: [statistics.mean(list(panel.values())) - 1.96 *
                                 statistics.stdev(panel.values()) / len(panel)**.5,
                                 statistics.mean(list(panel.values())) + 1.96 *
                                 statistics.stdev(panel.values()) / len(panel)**.5]
                             for p, panel in panels.items()},
                         "seed_panels_by_profile_bb": panels, "rows": rows})
    result = {"version": "1.9.1", "single_window_bb": [2.20, 2.30],
              "anchor_bb": 2.25, "seeds": SEEDS, "profiles": PROFILES,
              "edges": receipts, "rules_source": "assumed_for_research",
              "candidate_status": "projected_2.25_anchor_only; production_range_extension_off",
              "limitations": "Eight development seed panels per edge and scripted continuation. Projecting one anchor is a stress diagnostic, not a calibrated price-dependent range or platform EV."}
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, separators=(",", ":")) + "\n", encoding="utf-8")
    return [{"price_bb": r["price_bb"], "changed": r["changed_class_count"],
             "gain_by_profile_bb": r["gain_by_profile_bb"]} for r in receipts]


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    print(json.dumps(run(args.output)))
