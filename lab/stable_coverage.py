"""Observed decision coverage in complete scripted HU hands, both seats."""
from collections import Counter
from pathlib import Path
import json
import random

from hu_bot.config import Config
from hu_bot.engine import Engine
from hu_bot.poker import DECK
from lab.match import play_hand
from lab.profiles import Profile

PROFILES = ("tight", "call", "aggressive")
SEEDS = tuple(range(190601, 190625))


def run(output):
    output = Path(output)
    if output.exists():
        raise FileExistsError(output)
    nodes, postflop_cells, full_routes = Counter(), Counter(), Counter()
    fallback, decisions = 0, 0
    for profile in PROFILES:
        for seed in SEEDS:
            deck = random.Random(seed).sample(DECK, 52)
            for hero_seat in (0, 1):
                bot = Engine(Config(trace=False, retain_diagnostics=True,
                                    max_samples=128, decision_budget_ms=160,
                                    learn_opponent=False, adapt_ranges=False,
                                    adapt_responses=False, exploit_cap=0))
                bot.reset(f"coverage-{profile}-{seed}-{hero_seat}")
                opponent = Profile(profile, seed + 71)
                players = [opponent, opponent]
                players[hero_seat] = bot
                _, traces, result = play_hand(players, deck, 1,
                    button=seed % 2, stack=1000, bb=10, ante=0)
                hero_traces = [t for t in traces if t.get("match") == bot.match_id]
                decisions += len(hero_traces)
                fallback += sum(bool(t.get("fallback")) for t in hero_traces)
                for trace in hero_traces:
                    node = trace.get("node", "unknown")
                    nodes[node] += 1
                    if trace.get("street") != "preflop":
                        wet = bool(trace.get("features", {}).get("wet"))
                        spr = trace.get("spr", 0)
                        spr_bin = "low" if spr < 2 else "mid" if spr < 5 else "high"
                        aggressor = trace.get("last_aggressor")
                        aggressor_relation = ("none" if aggressor is None else
                                              "hero" if aggressor == hero_seat else "villain")
                        key = "|".join((node, spr_bin,
                            aggressor_relation,
                            "wet" if wet else "dry"))
                        postflop_cells[key] += 1
                pf_raises = sum(e["action"] == "raise" for e in
                    result["action_history"] if e["phase"] == "preflop")
                pot_type = "LIMP" if pf_raises == 0 else "SRP" if pf_raises == 1 else "RERAISED"
                pos = "IP" if hero_seat == seed % 2 else "OOP"
                streets = {e["phase"] for e in result["action_history"]
                    if e["seat"] == hero_seat and e["action"] not in
                    {"post_small_blind", "post_big_blind", "post_ante"}}
                if "flop" in streets:
                    full_routes[f"{pot_type}|{pos}|flop"] += 1
                if {"flop", "turn"}.issubset(streets):
                    full_routes[f"{pot_type}|{pos}|flop_turn"] += 1
                if {"flop", "turn", "river"}.issubset(streets):
                    full_routes[f"{pot_type}|{pos}|flop_turn_river"] += 1
    receipt = {"version": "1.9.0", "rules_source": "assumed_for_research",
        "profiles": PROFILES, "seeds": SEEDS, "hands": len(PROFILES)*len(SEEDS)*2,
        "decisions": decisions, "fallbacks": fallback,
        "nodes": dict(nodes), "postflop_cells": dict(postflop_cells),
        "full_routes": dict(full_routes),
        "limitations": "Fixed 100BB scripted profiles; empirical reach is not platform reach."}
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(receipt, indent=2) + "\n", encoding="utf-8")
    return {"hands": receipt["hands"], "decisions": decisions,
            "full_routes": receipt["full_routes"]}


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    print(json.dumps(run(args.output)))
