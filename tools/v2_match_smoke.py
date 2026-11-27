"""Short paired seat-swap matches for legal/completion smoke only."""

from __future__ import annotations

import json
from pathlib import Path
import random
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "submission"))
sys.path.insert(0, str(ROOT))

from hu_bot.poker import DECK  # noqa: E402
from hu_bot.v2_bot import V2HUBot  # noqa: E402
from lab.match import play_hand  # noqa: E402
from lab.profiles import Profile  # noqa: E402


class V2Player:
    def __init__(self, bot):
        self.bot = bot
        self.last_trace = {}

    def decide(self, state):
        action = self.bot.decide(state)
        self.last_trace = dict(self.bot.runtime.last_trace)
        return action


def run_match(seed, opponent_name, hero_seat, max_hands=100):
    rng = random.Random(seed)
    bot = V2HUBot()
    bot.on_match_start({"match_id": f"v220-smoke-{seed}-{hero_seat}",
                        "game_config": {"big_blind": 10},
                        "turn_timeout_ms": 2000})
    hero = V2Player(bot)
    villain = Profile(opponent_name, seed + 11)
    players = [villain, villain]
    players[hero_seat] = hero
    stacks = [1000, 1000]
    decisions = 0
    fallback = 0
    hands = 0
    for hand in range(1, max_hands + 1):
        if min(stacks) <= 0:
            break
        dealer = hand % 2
        bot.on_round_start({"state": {"hand_number": hand, "dealer_seat": dealer,
                                      "stacks": list(stacks), "big_blind": 10}})
        deck = rng.sample(DECK, 52)
        _, traces, result = play_hand(players, deck, hand_number=hand, button=dealer,
                                      stack=1000, bb=10, ante=0,
                                      starting_stacks=stacks)
        stacks = result["stacks"]
        bot.on_round_result({"round_id": f"r-{hand}", "result": result})
        hands += 1
        decisions += sum(1 for trace in traces if trace.get("path") == "v2")
        fallback += sum(bool(trace.get("fallback_reason")) for trace in traces)
    bot.on_match_end({})
    return {"seed": seed, "opponent": opponent_name, "hero_seat": hero_seat,
            "hands": hands, "final_stacks": stacks, "decisions": decisions,
            "fallbacks": fallback, "completed": True}


def main():
    rows = [run_match(seed, opponent, seat)
            for seed in (22101, 22102)
            for opponent in ("tight", "call", "aggressive")
            for seat in (0, 1)]
    print(json.dumps({"version": "2.2.0", "matches": rows,
                      "purpose": "legal/completion paired smoke; not strength evidence",
                      "limitations": "fixed 100BB cash-hand simulator, scripted profiles, no platform schedule"},
                     indent=2))


if __name__ == "__main__":
    main()
