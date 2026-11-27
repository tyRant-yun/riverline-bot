"""Measure scripted profile behavior on identical synthetic public states.

The uniformly dealt states diagnose whether parameter differences actually
produce distinct actions. They do not estimate real match frequencies.
"""
from __future__ import annotations

import argparse
from collections import Counter
import json
from pathlib import Path
import random
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "submission"))

from chipzen import GameState
from chipzen.models import Card

from hu_bot.poker import DECK, features
from lab.profile_family import ARCHETYPES, ProfileFamily, parameter_manifest


SPOTS = {"unopened": 0, "third_pot": 33, "pot": 100, "overbet": 150}


def _state(cards, bet):
    board = [Card.from_str(card) for card in cards[2:]]
    values = dict(hand_number=1, phase="river",
                  hole_cards=[Card.from_str(card) for card in cards[:2]],
                  board=board, pot=100+bet, your_stack=900,
                  opponent_stacks=[900], your_seat=0, dealer_seat=0,
                  to_call=bet, min_raise=max(20, 2*bet), max_raise=900,
                  valid_actions=["fold", "call", "raise"] if bet else ["check", "raise"],
                  action_history=[])
    return GameState(**values)


def calibrate(deals=3000, seed=7411):
    if deals < 100:
        raise ValueError("at least 100 deals required")
    rng = random.Random(seed)
    cards = [rng.sample(DECK, 7) for _ in range(deals)]
    output = {"seed": seed, "deals_per_spot": deals,
              "design": "uniform independent seven-card deals; identical deals by archetype",
              "parameters": parameter_manifest(), "spots": {}}
    for spot, bet in SPOTS.items():
        states = [_state(deal, bet) for deal in cards]
        strengths = [features(deal[:2], deal[2:]).strength for deal in cards]
        result = {}
        for name in ARCHETYPES:
            opponent = ProfileFamily(name, seed+19)
            count, weak_bets = Counter(), 0
            for state, strength in zip(states, strengths):
                action = opponent.decide(state)
                count[action.action] += 1
                if bet == 0 and strength < .50 and action.action == "raise":
                    weak_bets += 1
            result[name] = {"actions": dict(count),
                            "rates": {action: round(n/deals, 4)
                                      for action, n in sorted(count.items())},
                            "weak_bets": weak_bets if bet == 0 else None}
        output["spots"][spot] = {"bet": bet, "profiles": result,
                                  "strength_bins": dict(Counter(strengths))}
    return output


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--deals", type=int, default=3000)
    parser.add_argument("--seed", type=int, default=7411)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    result = calibrate(args.deals, args.seed)
    with open(args.output, "x", encoding="utf-8") as handle:
        json.dump(result, handle, indent=2)
    print(args.output)


if __name__ == "__main__":
    main()
