"""Fingerprint default-off legacy policies against a frozen import path."""
from collections import defaultdict
from itertools import combinations
import hashlib
import json

import hu_bot
from hu_bot.core import normalize
from hu_bot.poker import DECK, hand_class
from hu_bot.preflop import DATA, TABLE_SHA256, preflop_policy
from hu_bot.strategy import legalize
from lab.scenarios import BLINDS, entry, spot


CASES = {"BTN_FIRST_IN": [0], "BB_VS_LIMP": [1],
         "BB_VS_OPEN": [2, 2.25, 2.5, 3],
         "BTN_VS_ISO": [3.5, 5], "BTN_VS_3BET": [2, 3],
         "BB_VS_4BET": [2, 3], "BB_VS_LIMP_RERAISE": [3.5]}


def state(context, depth, price, cards):
    events = list(BLINDS)
    bets = [5, 10]
    if context == "BB_VS_LIMP":
        events.append(entry(0, "call", 5))
        bets[0] = 10
    elif context == "BB_VS_OPEN":
        amount = round(price*10)
        events.append(entry(0, "raise", amount))
        bets[0] = amount
    elif context == "BTN_VS_ISO":
        amount = round(price*10)
        events += [entry(0, "call", 5), entry(1, "raise", amount)]
        bets = [10, amount]
    elif context in {"BTN_VS_3BET", "BB_VS_4BET"}:
        opened = round(price*10)
        three = round(opened*3.5)
        events += [entry(0, "raise", opened), entry(1, "raise", three)]
        bets = [opened, three]
        if context == "BB_VS_4BET":
            four = round(three*2.25)
            events.append(entry(0, "raise", four))
            bets[0] = four
    elif context == "BB_VS_LIMP_RERAISE":
        four = round(35*2.25)
        events += [entry(0, "call", 5), entry(1, "raise", 35),
                   entry(0, "raise", four)]
        bets = [four, 35]
    seat = 0 if context.startswith("BTN") else 1
    owed = max(0, bets[1-seat]-bets[seat])
    raw = spot(" ".join(cards), your_seat=seat, pot=sum(bets),
               your_stack=depth*10-bets[seat],
               opponent_stacks=[depth*10-bets[1-seat]], to_call=owed,
               min_raise=max(bets)+10, max_raise=depth*10,
               valid_actions=["fold", "call", "raise"] if owed else ["check", "raise"],
               action_history=events)
    return normalize(raw, 10)


def main():
    representatives = {}
    for cards in combinations(DECK, 2):
        representatives.setdefault(hand_class(cards), cards)
    records = []
    for context, prices in CASES.items():
        for depth in (100, 60):
            for price in prices:
                for klass in DATA["classes"]:
                    s = state(context, depth, price, representatives[klass])
                    policy = preflop_policy(s).normalized()
                    actions = defaultdict(float)
                    for label, probability in policy.weights.items():
                        executable = legalize(label, s)[0].to_dict()
                        actions[json.dumps(executable, sort_keys=True)] += probability
                    records.append((context, depth, price, klass, policy.weights,
                                    sorted(actions.items())))
    fingerprint = hashlib.sha256(json.dumps(records, sort_keys=True,
                                            separators=(",", ":")).encode()).hexdigest()
    print(json.dumps({"import_path": hu_bot.__file__, "version": hu_bot.__version__,
                      "old_table_sha256": TABLE_SHA256, "cells": len(records),
                      "default_policy_and_legal_action_sha256": fingerprint}))


if __name__ == "__main__":
    main()
