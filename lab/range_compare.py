"""Freeze old/new 1326-combo range differences and independent holdout EV."""
from collections import defaultdict
import csv
import gzip
import json
from math import sqrt
from pathlib import Path
import statistics

from hu_bot.poker import hand_class
from hu_bot.preflop import DATA, lookup
from lab.build_range_spec import PROFILE_WEIGHTS, mapped_old
from lab.range_rollout import COMBOS, CONFIG, arrival_weight


def reach(context, depth, price):
    """Exact blocker-weighted hero arrival under the frozen mirror-chart model."""
    total = 0.0
    per_card = defaultdict(float)
    per_combo = {}
    for cards in COMBOS:
        weight = arrival_weight(context, depth, price, hand_class(cards))
        per_combo[cards] = weight
        total += weight
        for card in cards:
            per_card[card] += weight
    result = {}
    for cards in COMBOS:
        klass = hand_class(cards)
        own_path = 1.0
        if context == "BTN_VS_ISO":
            own_path = lookup("BTN_OPEN", depth, 2, klass).weights.get("CALL", 0)
        elif context == "BTN_VS_3BET":
            own_path = lookup("BTN_OPEN", depth, 2, klass).weights.get("RAISE_SMALL", 0)
        elif context == "BB_VS_4BET":
            row = lookup("BB_OPEN", depth, price, klass).weights
            own_path = row.get("RAISE_SMALL", 0)+row.get("RAISE_MEDIUM", 0)
        elif context == "BB_VS_LIMP_RERAISE":
            row = lookup("BB_LIMP", depth, 2, klass).weights
            own_path = row.get("RAISE_SMALL", 0)+row.get("RAISE_MEDIUM", 0)
        result[cards] = (total-per_card[cards[0]]-per_card[cards[1]]+
                         per_combo[cards])*own_path
    normalizer = sum(result.values())
    return {cards: value/normalizer for cards, value in result.items()}


def _rows(path, split):
    receipt = json.loads(Path(path).read_text(encoding="utf-8"))
    if receipt["split"] != split or receipt["config"] != CONFIG:
        raise ValueError((path, "receipt mismatch"))
    return {(r["context"], r["depth_bb"], r["price_bb"], r["class"],
             r["profile"], r["action"]): r for r in receipt["rows"]}


def build(train_path, holdout_path, spec_path, csv_path, summary_path):
    outputs = [Path(csv_path), Path(summary_path)]
    if any(p.exists() for p in outputs):
        raise FileExistsError("Comparison receipt already exists")
    train, holdout = _rows(train_path, "train"), _rows(holdout_path, "holdout")
    spec = json.loads(Path(spec_path).read_text(encoding="utf-8"))
    outputs[0].parent.mkdir(parents=True, exist_ok=True)
    summary = {"source": "exact_blocker_reach_plus_class_shared_action_values",
               "comparison": "v1.6.0 legacy chart vs v1.7.0 research candidate",
               "rules": spec["rules"], "profiles": PROFILE_WEIGHTS,
               "train_receipt": str(train_path), "holdout_receipt": str(holdout_path),
               "range_spec": str(spec_path), "nodes": []}
    fields = ["context", "depth_bb", "price_bb", "cards", "class",
              "conditional_weight", "old_policy", "candidate_policy",
              "policy_l1", "train_action_ev_bb", "holdout_action_ev_bb",
              "holdout_old_ev_bb", "holdout_candidate_ev_bb", "holdout_gain_bb",
              "ev_resolution"]
    with gzip.open(outputs[0], "wt", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        for context, prices in CONFIG["scenarios"].items():
            for depth in CONFIG["depths_bb"]:
                for price in prices:
                    actions = CONFIG["actions"][context]
                    table = spec["tables"][f"{context}:{depth}:{price}"]
                    weights = reach(context, depth, price)
                    class_weight = defaultdict(float)
                    for cards, weight in weights.items():
                        class_weight[hand_class(cards)] += weight
                    class_detail = {}
                    gain_panels = [0.0]*len(CONFIG["holdout_seeds"]*CONFIG["samples_per_seed"])
                    old_panels = [0.0]*len(gain_panels)
                    new_panels = [0.0]*len(gain_panels)
                    for index, klass in enumerate(DATA["classes"]):
                        old = mapped_old(context, depth, price, klass)
                        new = {a: p for a, p in zip(actions, table[index]) if p > 1e-10}
                        action_train, action_holdout = {}, {}
                        for action in actions:
                            action_train[action] = sum(PROFILE_WEIGHTS[profile]*train[
                                (context, depth, price, klass, profile, action)]["ev_bb"]
                                for profile in PROFILE_WEIGHTS)
                            action_holdout[action] = sum(PROFILE_WEIGHTS[profile]*holdout[
                                (context, depth, price, klass, profile, action)]["ev_bb"]
                                for profile in PROFILE_WEIGHTS)
                        old_ev = sum(p*action_holdout[a] for a, p in old.items())
                        new_ev = sum(p*action_holdout[a] for a, p in new.items())
                        class_detail[klass] = old, new, action_train, action_holdout, old_ev, new_ev
                        for panel in range(len(gain_panels)):
                            old_value = sum(PROFILE_WEIGHTS[profile]*old.get(action, 0)*
                                holdout[(context, depth, price, klass, profile, action)]["values_bb"][panel]
                                for profile in PROFILE_WEIGHTS for action in actions)
                            new_value = sum(PROFILE_WEIGHTS[profile]*new.get(action, 0)*
                                holdout[(context, depth, price, klass, profile, action)]["values_bb"][panel]
                                for profile in PROFILE_WEIGHTS for action in actions)
                            old_panels[panel] += class_weight[klass]*old_value
                            new_panels[panel] += class_weight[klass]*new_value
                            gain_panels[panel] += class_weight[klass]*(new_value-old_value)
                    for cards in COMBOS:
                        klass = hand_class(cards)
                        old, new, action_train, action_holdout, old_ev, new_ev = class_detail[klass]
                        writer.writerow({"context": context, "depth_bb": depth,
                            "price_bb": price, "cards": " ".join(cards), "class": klass,
                            "conditional_weight": weights[cards],
                            "old_policy": json.dumps(old, sort_keys=True),
                            "candidate_policy": json.dumps(new, sort_keys=True),
                            "policy_l1": sum(abs(old.get(a, 0)-new.get(a, 0)) for a in actions),
                            "train_action_ev_bb": json.dumps(action_train, sort_keys=True),
                            "holdout_action_ev_bb": json.dumps(action_holdout, sort_keys=True),
                            "holdout_old_ev_bb": old_ev,
                            "holdout_candidate_ev_bb": new_ev,
                            "holdout_gain_bb": new_ev-old_ev,
                            "ev_resolution": "class-shared 32 paired worlds per profile"})
                    gain = statistics.mean(gain_panels)
                    se = statistics.stdev(gain_panels)/sqrt(len(gain_panels))
                    summary["nodes"].append({"context": context, "depth_bb": depth,
                        "price_bb": price, "changed_classes": sum(
                            class_detail[k][0] != class_detail[k][1] for k in DATA["classes"]),
                        "conditional_old_ev_bb": statistics.mean(old_panels),
                        "conditional_candidate_ev_bb": statistics.mean(new_panels),
                        "paired_gain_bb": gain, "paired_gain_se_bb": se,
                        "paired_normal_95_ci_bb": [gain-1.96*se, gain+1.96*se],
                        "sampling_panels": len(gain_panels)})
    outputs[1].write_text(json.dumps(summary, indent=2)+"\n", encoding="utf-8")
    return {"combo_nodes": len(COMBOS)*len(summary["nodes"]),
            "scenario_nodes": len(summary["nodes"]),
            "positive_holdout_nodes": sum(n["paired_gain_bb"] > 0 for n in summary["nodes"])}


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    for name in ("train", "holdout", "spec", "csv", "summary"):
        parser.add_argument(name, type=Path)
    args = parser.parse_args()
    print(json.dumps(build(args.train, args.holdout, args.spec,
                           args.csv, args.summary)))
