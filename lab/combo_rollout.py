"""Exact 1326-combo action-value audit with independent sampled continuations.

Two boards per profile and combo give diagnostic evidence, not a precise
per-combo solution. Full class-level development and holdout are separate.
"""
from bisect import bisect_left
import gzip
from itertools import combinations
import json
from pathlib import Path
import random

from hu_bot.poker import DECK, hand_class
from lab.range_rollout import (COMBOS, CONFIG, _seed, arrival_sampler,
                               hero_seat, one_rollout)


PROFILE_WEIGHTS = {"tight": .50, "call": .30, "aggressive": .20}
SEEDS = [190001, 190002]


def combo_world(context, cards, identity, sampler):
    rng = random.Random(identity)
    cumulative, total = sampler
    while True:
        opponent = COMBOS[bisect_left(cumulative, rng.random()*total)]
        if set(cards).isdisjoint(opponent):
            break
    blocked = set(cards+opponent)
    board = rng.sample([card for card in DECK if card not in blocked], 5)
    if hero_seat(context) == 0:
        return list(cards+opponent)+board
    return list(opponent+cards)+board


def audit(output):
    output = Path(output)
    if output.exists():
        raise FileExistsError(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    row_count = 0
    with gzip.open(output, "wt", encoding="utf-8", compresslevel=6) as stream:
        stream.write(json.dumps({"kind": "header", "source": "scripted_full_hand",
                                 "seeds": SEEDS, "profiles": PROFILE_WEIGHTS,
                                 "boards_per_profile": len(SEEDS),
                                 "rules": {"hu": True, "ante": 0, "bb": 10,
                                           "rake": 0}})+"\n")
        for context, prices in CONFIG["scenarios"].items():
            for depth in CONFIG["depths_bb"]:
                for price in prices:
                    sampler = arrival_sampler(context, depth, price)
                    for cards in combinations(DECK, 2):
                        by_action = {action: {} for action in CONFIG["actions"][context]}
                        for profile in PROFILE_WEIGHTS:
                            for action in by_action:
                                by_action[action][profile] = []
                            for seed in SEEDS:
                                identity = _seed("combo", context, depth, price, cards, seed)
                                deck = combo_world(context, cards, identity, sampler)
                                for action in by_action:
                                    ev, _ = one_rollout(context, depth, price, action,
                                                        deck, profile, identity)
                                    by_action[action][profile].append(ev)
                        means = {action: sum(
                            weight*sum(by_action[action][profile])/len(SEEDS)
                            for profile, weight in PROFILE_WEIGHTS.items())
                            for action in by_action}
                        stream.write(json.dumps({"kind": "combo", "context": context,
                                                 "depth_bb": depth, "price_bb": price,
                                                 "cards": cards, "class": hand_class(cards),
                                                 "action_ev_bb": means,
                                                 "values_bb_by_action_profile": by_action},
                                                separators=(",", ":"))+"\n")
                        row_count += 1
    return {"combo_nodes": row_count, "actions": "all_menu_actions",
            "boards_per_profile": len(SEEDS), "output": str(output)}


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    print(json.dumps(audit(args.output)))
