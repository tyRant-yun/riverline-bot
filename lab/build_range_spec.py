"""Generate a complete, default-off RangeSpec from frozen development rollouts."""
from collections import Counter
import hashlib
import json
from math import sqrt
from pathlib import Path
import statistics

from hu_bot.preflop import DATA, TABLE_SHA256, lookup
from lab.range_rollout import CONFIG


PROFILE_WEIGHTS = {"tight": .50, "call": .30, "aggressive": .20}
GAIN_FLOOR_BB = .15
MAX_PROFILE_LOSS_BB = .50
TRANSFER = .20


def old_policy(context, depth, price):
    chart = {"BTN_FIRST_IN": ("BTN_OPEN", 2),
             "BB_VS_LIMP": ("BB_LIMP", 2),
             "BB_VS_OPEN": ("BB_OPEN", price),
             "BTN_VS_ISO": ("BTN_ISOLATION", price),
             "BTN_VS_3BET": ("VS_3BET", 3.5),
             "BB_VS_4BET": ("VS_4BET", 2.25),
             "BB_VS_LIMP_RERAISE": ("VS_3BET", 2.25)}[context]
    return chart


def mapped_old(context, depth, price, klass):
    chart_context, chart_price = old_policy(context, depth, price)
    weights = lookup(chart_context, depth, chart_price, klass).weights
    mapping = {
        "BTN_FIRST_IN": {"FOLD": "FOLD", "CALL": "CALL", "RAISE_SMALL": "OPEN_225"},
        "BB_VS_LIMP": {"CHECK": "CHECK", "RAISE_SMALL": "ISO_35",
                       "RAISE_MEDIUM": "ISO_35"},
        "BB_VS_OPEN": {"FOLD": "FOLD", "CALL": "CALL", "RAISE_SMALL": "THREEBET_33",
                       "RAISE_MEDIUM": "THREEBET_33", "ALL_IN": "ALL_IN"},
        "BTN_VS_ISO": {"FOLD": "FOLD", "CALL": "CALL", "RAISE_SMALL": "RERAISE_280",
                       "RAISE_MEDIUM": "RERAISE_280", "ALL_IN": "ALL_IN"},
        "BTN_VS_3BET": {"FOLD": "FOLD", "CALL": "CALL", "RAISE_SMALL": "RERAISE_225",
                        "RAISE_MEDIUM": "RERAISE_225", "ALL_IN": "ALL_IN"},
        "BB_VS_4BET": {"FOLD": "FOLD", "CALL": "CALL", "RAISE_SMALL": "RERAISE_225",
                        "RAISE_MEDIUM": "RERAISE_225", "ALL_IN": "ALL_IN"},
        "BB_VS_LIMP_RERAISE": {"FOLD": "FOLD", "CALL": "CALL",
                               "RAISE_SMALL": "RERAISE_225",
                               "RAISE_MEDIUM": "RERAISE_225", "ALL_IN": "ALL_IN"},
    }[context]
    result = Counter()
    for action, p in weights.items():
        if action not in mapping:
            raise ValueError((context, action, "old action not mapped"))
        result[mapping[action]] += p
    return dict(result)


def _key(context, depth, price, klass, profile, action):
    return context, depth, price, klass, profile, action


def load_rows(receipt_path):
    receipt = json.loads(Path(receipt_path).read_text())
    if receipt["split"] != "train" or receipt["config"] != CONFIG:
        raise ValueError("Expected frozen train receipt and configuration")
    rows = {_key(r["context"], r["depth_bb"], r["price_bb"], r["class"],
                 r["profile"], r["action"]): r for r in receipt["rows"]}
    return rows


def one_cell(rows, context, depth, price, klass):
    old = mapped_old(context, depth, price, klass)
    actions = CONFIG["actions"][context]
    if set(old)-set(actions):
        raise ValueError((context, old, "missing legacy executable action"))
    gains = {}
    per_action = {}
    for action in actions:
        by_profile = {}
        weighted = []
        for profile, profile_weight in PROFILE_WEIGHTS.items():
            result = rows[_key(context, depth, price, klass, profile, action)]
            action_values = result["values_bb"]
            baseline = [sum(old.get(old_action, 0)*rows[
                _key(context, depth, price, klass, profile, old_action)]["values_bb"][i]
                for old_action in old) for i in range(len(action_values))]
            diff = [value-baseline[i] for i, value in enumerate(action_values)]
            by_profile[profile] = {"ev_bb": statistics.mean(action_values),
                                   "ev_se_bb": result["se_bb"],
                                   "gain_bb": statistics.mean(diff),
                                   "gain_se_bb": statistics.stdev(diff)/sqrt(len(diff))}
            if not weighted:
                weighted = [0.0]*len(diff)
            weighted = [v+profile_weight*d for v, d in zip(weighted, diff)]
        gain = statistics.mean(weighted)
        se = statistics.stdev(weighted)/sqrt(len(weighted))
        gains[action] = (gain, se)
        per_action[action] = {"ev_by_profile": by_profile,
                              "weighted_gain_bb": gain, "gain_se_bb": se,
                              "gain_95_ci_bb": [gain-1.96*se, gain+1.96*se]}
    eligible = []
    for action in actions:
        gain, se = gains[action]
        if (gain > GAIN_FLOOR_BB+se and
                min(per_action[action]["ev_by_profile"][p]["gain_bb"]
                    for p in PROFILE_WEIGHTS) > -MAX_PROFILE_LOSS_BB):
            eligible.append(action)
    chosen = max(eligible, key=lambda label: gains[label][0]) if eligible else None
    final = dict(old)
    if chosen is not None:
        final = {action: (1-TRANSFER)*old.get(action, 0)
                 +TRANSFER*(action == chosen) for action in actions}
    final = {action: round(value, 8) for action, value in final.items() if value > 1e-9}
    total = sum(final.values())
    final = {action: value/total for action, value in final.items()}
    return final, {"old": old, "candidate": final, "selected_action": chosen,
                   "actions": per_action, "decision": "limited_mix" if chosen else "keep_old"}


def build(train_path, spec_path, evidence_path):
    for path in (spec_path, evidence_path):
        if Path(path).exists():
            raise FileExistsError(path)
    rows = load_rows(train_path)
    spec = {"schema_version": 1, "version": "1.7.0",
            "source": "scripted_full_hand_action_rollout_research_only",
            "matched_solver_unavailable": True,
            "old_table_sha256": TABLE_SHA256,
            "rules": {"HU": True, "ante": 0, "sb": 5, "bb": 10,
                      "rake": 0, "source": "assumed_for_research"},
            "depth_anchors_bb": CONFIG["depths_bb"],
            "price_anchors_by_context": CONFIG["scenarios"],
            "actions_by_context": CONFIG["actions"],
            "classes": DATA["classes"], "tables": {}}
    evidence = {"config": {"profile_weights": PROFILE_WEIGHTS,
                           "gain_floor_bb": GAIN_FLOOR_BB,
                           "max_profile_loss_bb": MAX_PROFILE_LOSS_BB,
                           "transfer": TRANSFER,
                           "train_receipt": str(train_path)},
                "rows": []}
    for context, prices in CONFIG["scenarios"].items():
        for depth in CONFIG["depths_bb"]:
            for price in prices:
                table = []
                for klass in DATA["classes"]:
                    final, detail = one_cell(rows, context, depth, price, klass)
                    table.append([final.get(action, 0) for action in CONFIG["actions"][context]])
                    evidence["rows"].append({"context": context, "depth_bb": depth,
                                             "price_bb": price, "class": klass, **detail})
                spec["tables"][f"{context}:{depth}:{price}"] = table
    encoded = (json.dumps(spec, separators=(",", ":"), sort_keys=True)+"\n").encode()
    Path(spec_path).parent.mkdir(parents=True, exist_ok=True)
    Path(spec_path).write_bytes(encoded)
    evidence["range_spec_sha256"] = hashlib.sha256(encoded).hexdigest()
    Path(evidence_path).parent.mkdir(parents=True, exist_ok=True)
    Path(evidence_path).write_text(json.dumps(evidence, separators=(",", ":"))+"\n")
    return {"table_sha256": evidence["range_spec_sha256"],
            "cells": len(evidence["rows"]),
            "changed": sum(r["selected_action"] is not None for r in evidence["rows"])}


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("train", type=Path)
    parser.add_argument("spec", type=Path)
    parser.add_argument("evidence", type=Path)
    args = parser.parse_args()
    print(json.dumps(build(args.train, args.spec, args.evidence)))
