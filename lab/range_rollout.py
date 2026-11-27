"""Paired first-action rollouts over all 169 classes under named script policies.

These are complete four-street hands, including future bets and showdown.
They are NOT a solved HU game: the continuation players are fixed scripts.
"""
from bisect import bisect_left
from collections import defaultdict
from itertools import combinations
import hashlib
import json
from pathlib import Path
import random
import statistics

from chipzen import Action

from hu_bot.poker import DECK, hand_class
from hu_bot.preflop import DATA, lookup
from hu_bot.strategy import preflop_factor
from lab.match import play_hand
from lab.profiles import Profile


CONFIG = {
    "version": 1, "bb_chips": 10, "ante_chips": 0,
    "depths_bb": [100, 60],
    "scenarios": {
        "BTN_FIRST_IN": [0],
        "BB_VS_LIMP": [1],
        "BB_VS_OPEN": [2, 2.25, 2.5, 3],
        "BTN_VS_ISO": [3.5, 5],
        "BTN_VS_3BET": [2, 3],
        "BB_VS_4BET": [2, 3],
        "BB_VS_LIMP_RERAISE": [3.5],
    },
    "actions": {
        "BTN_FIRST_IN": ["FOLD", "CALL", "OPEN_2", "OPEN_225", "OPEN_25"],
        "BB_VS_LIMP": ["CHECK", "ISO_3", "ISO_35", "ISO_4", "ISO_5"],
        "BB_VS_OPEN": ["FOLD", "CALL", "THREEBET_3", "THREEBET_33", "THREEBET_35", "THREEBET_4"],
        "BTN_VS_ISO": ["FOLD", "CALL", "RERAISE_225", "RERAISE_280"],
        "BTN_VS_3BET": ["FOLD", "CALL", "RERAISE_225"],
        "BB_VS_4BET": ["FOLD", "CALL", "RERAISE_225", "ALL_IN"],
        "BB_VS_LIMP_RERAISE": ["FOLD", "CALL", "RERAISE_225"],
    },
    "continuation_profiles": ["tight", "call", "aggressive"],
    "train_seeds": [170001, 170002, 170003, 170004, 170005, 170006, 170007, 170008],
    "holdout_seeds": [180001, 180002, 180003, 180004, 180005, 180006, 180007, 180008],
    "samples_per_seed": 4,
    "range_source": "v1.6.0 original heuristic policy, conditioned on scripted prefix; blockers removed",
    "accounting": "current decision EV: full-hand net from hand start plus already committed hero chips, in BB",
    "limitations": "Forced public prefix, frozen scripted future play, no matched solver or platform rules.",
}


COMBOS = tuple(combinations(DECK, 2))
CLASS_COMBOS = defaultdict(list)
for _combo in COMBOS:
    CLASS_COMBOS[hand_class(_combo)].append(_combo)


def hero_seat(context):
    return 0 if context.startswith("BTN") else 1


def prefix(context, price):
    if context in {"BTN_FIRST_IN", "BB_VS_LIMP", "BB_VS_OPEN"}:
        return ([] if context == "BTN_FIRST_IN" else
                [(0, "CALL")] if context == "BB_VS_LIMP" else
                [(0, f"OPEN_{round(price*100)}")])
    if context == "BTN_VS_ISO":
        return [(0, "CALL"), (1, f"ISO_{round(price*100)}")]
    if context == "BTN_VS_3BET":
        return [(0, f"OPEN_{round(price*100)}"), (1, "THREEBET_35")]
    if context == "BB_VS_4BET":
        return [(0, f"OPEN_{round(price*100)}"), (1, "THREEBET_35"),
                (0, "RERAISE_225")]
    if context == "BB_VS_LIMP_RERAISE":
        return [(0, "CALL"), (1, "ISO_350"), (0, "RERAISE_225")]
    raise ValueError(context)


def action_for(label, state):
    if label in {"FOLD", "CALL", "CHECK"}:
        return Action(label.lower())
    if "raise" not in state.valid_actions:
        return Action.call() if "call" in state.valid_actions else Action.check()
    if label == "ALL_IN":
        target = state.max_raise
    elif label.startswith("OPEN_"):
        target = round(preflop_factor(label)*10)
    elif label.startswith("ISO_"):
        target = round(preflop_factor(label)*10)
    elif label.startswith("THREEBET_"):
        previous = max(e["amount"] for e in state.action_history
                       if e["phase"] == "preflop" and e["action"] == "raise")
        target = round(previous*preflop_factor(label))
    elif label.startswith("RERAISE_"):
        previous = max(e["amount"] for e in state.action_history
                       if e["phase"] == "preflop" and e["action"] == "raise")
        target = round(previous*preflop_factor(label))
    else:
        raise ValueError(label)
    return Action.raise_to(max(state.min_raise, min(state.max_raise, target)))


class ScriptedPlayer:
    def __init__(self, seat, hero, script, choice, profile, seed):
        self.seat, self.hero, self.script, self.choice = seat, hero, script, choice
        self.profile = Profile(profile, seed)
        self.sunk_at_target = None
        self.target_action = None

    def decide(self, state):
        voluntary = [e for e in state.action_history if e["phase"] == "preflop"
                     and not e["action"].startswith("post_")]
        if state.phase == "preflop" and len(voluntary) < len(self.script):
            expected, label = self.script[len(voluntary)]
            if expected != self.seat:
                raise AssertionError("Unexpected actor in scripted prefix")
            return action_for(label, state)
        if state.phase == "preflop" and len(voluntary) == len(self.script) and self.hero:
            self.sunk_at_target = self.starting_stack-state.your_stack
            self.target_action = action_for(self.choice, state)
            return self.target_action
        return self.profile.decide(state)


def arrival_weight(context, depth, price, klass):
    if context == "BTN_FIRST_IN":
        return 1.0
    if context == "BB_VS_LIMP":
        return lookup("BTN_OPEN", depth, 2, klass).weights.get("CALL", 0)
    if context == "BB_VS_OPEN":
        return lookup("BTN_OPEN", depth, 2, klass).weights.get("RAISE_SMALL", 0)
    if context == "BTN_VS_ISO":
        row = lookup("BB_LIMP", depth, 2, klass).weights
        return row.get("RAISE_SMALL", 0)+row.get("RAISE_MEDIUM", 0)
    if context == "BTN_VS_3BET":
        row = lookup("BB_OPEN", depth, price, klass).weights
        return row.get("RAISE_SMALL", 0)+row.get("RAISE_MEDIUM", 0)
    if context == "BB_VS_4BET":
        opened = lookup("BTN_OPEN", depth, 2, klass).weights.get("RAISE_SMALL", 0)
        row = lookup("VS_3BET", depth, 3.5, klass).weights
        return opened*(row.get("RAISE_SMALL", 0)+row.get("ALL_IN", 0))
    if context == "BB_VS_LIMP_RERAISE":
        limped = lookup("BTN_OPEN", depth, 2, klass).weights.get("CALL", 0)
        row = lookup("BTN_ISOLATION", depth, price, klass).weights
        return limped*(row.get("RAISE_SMALL", 0)+row.get("RAISE_MEDIUM", 0))
    raise ValueError(context)


def arrival_sampler(context, depth, price):
    cumulative, total = [], 0.0
    for cards in COMBOS:
        total += arrival_weight(context, depth, price, hand_class(cards))
        cumulative.append(total)
    if total <= 0:
        raise ValueError("Zero arrival range")
    return cumulative, total


def world(context, depth, price, klass, seed, sampler):
    rng = random.Random(seed)
    hero = rng.choice(CLASS_COMBOS[klass])
    cumulative, total = sampler
    while True:
        opponent = COMBOS[bisect_left(cumulative, rng.random()*total)]
        if set(hero).isdisjoint(opponent):
            break
    blocked = set(hero+opponent)
    board = rng.sample([card for card in DECK if card not in blocked], 5)
    if hero_seat(context) == 0:
        return list(hero+opponent)+board
    return list(opponent+hero)+board


def one_rollout(context, depth, price, label, deck, profile, seed):
    script = prefix(context, price)
    seat = hero_seat(context)
    players = [ScriptedPlayer(i, i == seat, script, label, profile, seed+100*i)
               for i in (0, 1)]
    for player in players:
        player.starting_stack = depth*10
    net, _, _ = play_hand(players, deck, 1, button=0, stack=depth*10, bb=10, ante=0)
    target = players[seat]
    if target.sunk_at_target is None:
        raise AssertionError((context, label, "target action not reached"))
    return (net[seat]+target.sunk_at_target)/10, target.target_action.to_dict()


def _seed(*parts):
    return int.from_bytes(hashlib.sha256(json.dumps(parts).encode()).digest()[:8], "big")


def run_split(output, split, classes=None):
    output = Path(output)
    if output.exists():
        raise FileExistsError(output)
    if split not in {"train", "holdout"}:
        raise ValueError(split)
    classes = tuple(DATA["classes"] if classes is None else classes)
    rows = []
    for context, prices in CONFIG["scenarios"].items():
        for depth in CONFIG["depths_bb"]:
            for price in prices:
                sampler = arrival_sampler(context, depth, price)
                for klass in classes:
                    for profile in CONFIG["continuation_profiles"]:
                        samples = {action: [] for action in CONFIG["actions"][context]}
                        actual = {}
                        for seed in CONFIG[f"{split}_seeds"]:
                            for sample in range(CONFIG["samples_per_seed"]):
                                identity = _seed(context, depth, price, klass, seed, sample)
                                deck = world(context, depth, price, klass, identity, sampler)
                                for action in samples:
                                    ev, executed = one_rollout(context, depth, price, action,
                                                               deck, profile, identity)
                                    samples[action].append(ev)
                                    actual[action] = executed
                        for action, values in samples.items():
                            rows.append({"context": context, "depth_bb": depth,
                                         "price_bb": price, "class": klass,
                                         "profile": profile, "action": action,
                                         "executed_example": actual[action],
                                         "n": len(values), "ev_bb": statistics.mean(values),
                                         "sd_bb": statistics.stdev(values),
                                         "se_bb": statistics.stdev(values)/len(values)**.5,
                                         "values_bb": values})
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps({"config": CONFIG, "split": split,
                                  "rows": rows}, separators=(",", ":"))+"\n", encoding="utf-8")
    return {"rows": len(rows), "samples": sum(row["n"] for row in rows)}


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("split", choices=("train", "holdout"))
    parser.add_argument("output", type=Path)
    parser.add_argument("--classes", nargs="+")
    args = parser.parse_args()
    print(json.dumps(run_split(args.output, args.split, args.classes)))
