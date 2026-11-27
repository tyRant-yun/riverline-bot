"""Fresh-seed, all-class audit of only 100BB BB versus a 2.25BB open.

This evaluates the frozen v1.9.0 research table, not a runtime promotion.
The opponent continuation and arrival range are declared local assumptions.
"""

from collections import defaultdict
from pathlib import Path
import hashlib
import json
import statistics

from hu_bot.poker import hand_class, preflop_score
from hu_bot.preflop import DATA
from hu_bot.range_spec import _spec
from lab.build_range_spec import mapped_old
from lab.range_compare import reach
from lab.range_rollout import CLASS_COMBOS, _seed, arrival_sampler, one_rollout, world


CONTEXT, DEPTH, PRICE = "BB_VS_OPEN", 100, 2.25
PROFILES = ("tight", "call", "aggressive")
SEEDS = tuple(range(191301, 191309))


def run(output):
    output = Path(output)
    if output.exists():
        raise FileExistsError(output)
    spec = _spec()
    labels = spec["actions_by_context"][CONTEXT]
    table = spec["tables"][f"{CONTEXT}:{DEPTH}:{PRICE}"]
    sampler = arrival_sampler(CONTEXT, DEPTH, PRICE)
    class_reach = defaultdict(float)
    for cards, weight in reach(CONTEXT, DEPTH, PRICE).items():
        class_reach[hand_class(cards)] += weight
    rows = []
    node_panels = {profile: {seed: 0.0 for seed in SEEDS} for profile in PROFILES}
    for index, klass in enumerate(DATA["classes"]):
        old = mapped_old(CONTEXT, DEPTH, PRICE, klass)
        new = dict(zip(labels, table[index]))
        delta = {label: new.get(label, 0) - old.get(label, 0) for label in labels}
        relevant = [label for label in labels if abs(delta[label]) > 1e-10]
        score = preflop_score(CLASS_COMBOS[klass][0])
        bucket = "value_strength" if score >= .8 else (
            "low_strength_bluff_risk" if score < .6 else "middle_strength")
        row = {"class": klass, "reach_weight": class_reach[klass],
               "old": old, "research_candidate": new,
               "heuristic_preflop_score": score, "strength_bucket": bucket,
               "old_3bet_weight": sum(value for label, value in old.items()
                                        if label.startswith("THREEBET_")),
               "research_3bet_weight": sum(value for label, value in new.items()
                                             if label.startswith("THREEBET_")),
               "changed": bool(relevant), "policy_l1": sum(abs(x) for x in delta.values()),
               "action_ev_by_profile_bb": {}, "gain_by_profile_bb": {},
               "weighted_gain_by_profile_bb": {}}
        if relevant:
            for profile in PROFILES:
                action_values = {label: [] for label in relevant}
                gains = []
                for seed in SEEDS:
                    identity = _seed("v191-one-window", CONTEXT, DEPTH, PRICE,
                                     klass, profile, seed)
                    deck = world(CONTEXT, DEPTH, PRICE, klass, identity, sampler)
                    values = {label: one_rollout(CONTEXT, DEPTH, PRICE, label,
                                                 deck, profile, identity)[0]
                              for label in relevant}
                    for label, value in values.items():
                        action_values[label].append(value)
                    gain = sum(delta[label] * values[label] for label in relevant)
                    gains.append(gain)
                    node_panels[profile][seed] += class_reach[klass] * gain
                row["action_ev_by_profile_bb"][profile] = {
                    label: {"mean": statistics.mean(values), "seed_values": values}
                    for label, values in action_values.items()}
                row["gain_by_profile_bb"][profile] = statistics.mean(gains)
                row["weighted_gain_by_profile_bb"][profile] = (
                    class_reach[klass] * statistics.mean(gains))
        rows.append(row)
    summary = {}
    for profile, panels in node_panels.items():
        values = list(panels.values())
        mean = statistics.mean(values)
        se = statistics.stdev(values) / len(values)**.5
        summary[profile] = {"mean_gain_bb_per_arrival": mean,
                            "normal_95_interval_bb": [mean - 1.96 * se,
                                                      mean + 1.96 * se],
                            "seed_panels_bb": panels,
                            "worst_classes": [
                                {"class": row["class"], "contribution_bb":
                                 row["weighted_gain_by_profile_bb"].get(profile, 0)}
                                for row in sorted(rows, key=lambda x:
                                                  x["weighted_gain_by_profile_bb"].get(profile, 0))[:12]]}
    structure = {}
    for bucket in ("value_strength", "middle_strength", "low_strength_bluff_risk"):
        selected = [row for row in rows if row["strength_bucket"] == bucket]
        structure[bucket] = {
            "class_count": len(selected),
            "changed_class_count": sum(row["changed"] for row in selected),
            "reach_weight": sum(row["reach_weight"] for row in selected),
            "weighted_old_3bet": sum(row["reach_weight"] * row["old_3bet_weight"]
                                     for row in selected),
            "weighted_research_3bet": sum(row["reach_weight"] *
                                          row["research_3bet_weight"] for row in selected),
            "weighted_gain_by_profile_bb": {
                profile: sum(row["weighted_gain_by_profile_bb"].get(profile, 0)
                             for row in selected) for profile in PROFILES}}
    result = {"version": "1.9.1", "node": {"context": CONTEXT,
              "depth_bb": DEPTH, "open_raise_to_bb": PRICE, "ante_bb": 0},
              "seeds": SEEDS, "profiles": PROFILES,
              "class_count": len(rows),
              "changed_class_count": sum(r["changed"] for r in rows),
              "summary": summary, "structure": structure, "rows": rows,
              "research_table_sha256": hashlib.sha256(
                  Path(__file__).resolve().parents[1].joinpath(
                      "submission/hu_bot/range_spec_v183.json").read_bytes()).hexdigest(),
              "rules_source": "assumed_for_research",
              "limitations": "Eight fresh seed panels per class/profile, paired actions on each deck, frozen scripted continuation and assumed arrival range. Not platform or solver EV; candidate table remains disabled."}
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, separators=(",", ":")) + "\n", encoding="utf-8")
    return {"classes": len(rows), "changed": result["changed_class_count"],
            "summary": summary}


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    print(json.dumps(run(args.output)))
