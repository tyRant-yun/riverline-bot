"""Paired complete-hand research for two reachable single-raised flop routes."""
from collections import Counter
from pathlib import Path
import json
import random
import statistics

from hu_bot.config import Config
from hu_bot.core import normalize
from hu_bot.engine import Engine
from hu_bot.poker import DECK
from hu_bot.strategy import legalize
from lab.match import play_hand
from lab.profiles import Profile

ROUTES = {"SRP_IP_FREE": ("CHECK", "BET_SMALL", "BET_MEDIUM", "BET_LARGE"),
          "LIMP_OOP_FREE": ("CHECK", "BET_SMALL", "BET_MEDIUM", "BET_LARGE")}
PROFILES = ("tight", "call", "aggressive")
SEEDS = tuple(range(190301, 190313))


class Probe:
    def __init__(self, route, force, seed, hero_seat):
        self.route, self.force, self.hero_seat = route, force, hero_seat
        self.engine = Engine(Config(trace=False, retain_diagnostics=True,
                                    max_samples=128, decision_budget_ms=160,
                                    learn_opponent=False, adapt_ranges=False,
                                    adapt_responses=False, exploit_cap=0))
        self.engine.reset(f"route-{seed}-{hero_seat}")
        self.opponent = self.engine.opponent
        self.match_id = self.engine.match_id
        self.last_trace = {}
        self.reached = False
        self.executed = None
        self.made = None

    def decide(self, state):
        action = self.engine.decide(state)
        self.last_trace = self.engine.last_trace.copy()
        target_node = ("SRP_FLOP_IP" if self.route == "SRP_IP_FREE"
                       else "LIMP_FLOP_OOP")
        target_position = (state.your_seat == state.dealer_seat
                           if self.route == "SRP_IP_FREE" else
                           state.your_seat != state.dealer_seat)
        if (not self.reached and state.phase == "flop" and
                target_position and target_node == self.last_trace.get("node")):
            facing = state.to_call > 0
            wanted = self.route.endswith("FACING")
            if facing == wanted:
                self.reached = True
                self.made = self.last_trace.get("features", {}).get("made")
                if self.force is not None:
                    forced, fallback = legalize(self.force, normalize(state,
                                                             self.engine.big_blind))
                    if not fallback:
                        action = forced
                        self.last_trace["action"] = action.to_dict()
                        self.last_trace["forced_label"] = self.force
                self.executed = action.to_dict()
        return action


def one(route, force, profile, seed, hero_seat):
    rng = random.Random(seed)
    deck = rng.sample(DECK, 52)
    bot = Probe(route, force, seed, hero_seat)
    villain = Profile(profile, seed + 91)
    players = [villain, villain]
    players[hero_seat] = bot
    button = hero_seat if route == "SRP_IP_FREE" else 1 - hero_seat
    net, traces, result = play_hand(players, deck, 1, button=button,
                                    stack=1000, bb=10, ante=0)
    streets = {event["phase"] for event in result["action_history"]
               if event["seat"] == hero_seat and event["action"] not in
               {"post_small_blind", "post_big_blind", "post_ante"}}
    return {"reached": bot.reached, "executed": bot.executed,
            "made": bot.made, "net_bb": net[hero_seat] / 10,
            "hero_streets": sorted(streets),
            "three_streets": {"flop", "turn", "river"}.issubset(streets),
            "fallbacks": sum(bool(t.get("fallback")) for t in traces
                             if t.get("match") == bot.match_id),
            "max_ms": max((t.get("elapsed_ms", 0) for t in traces
                           if t.get("match") == bot.match_id), default=0),
            "nodes": [t.get("node") for t in traces if t.get("match") == bot.match_id]}


def run(output, seeds=SEEDS):
    output = Path(output)
    if output.exists():
        raise FileExistsError(output)
    rows, coverage = [], Counter()
    for route, choices in ROUTES.items():
        for profile in PROFILES:
            for seed in seeds:
                for hero_seat in (0, 1):
                    arms = {label: one(route, label, profile, seed, hero_seat)
                            for label in choices}
                    baseline = one(route, None, profile, seed, hero_seat)
                    for node in baseline["nodes"]:
                        coverage[node] += 1
                    if not baseline["reached"]:
                        continue
                    if not all(arm["reached"] for arm in arms.values()):
                        raise AssertionError("Prefix divergence before intervention")
                    rows.append({"route": route, "profile": profile, "seed": seed,
                                 "hero_seat": hero_seat, "made": baseline["made"],
                                 "baseline": baseline, "alternatives": arms})
    summaries = []
    for route, choices in ROUTES.items():
        for profile in PROFILES:
            selected = [row for row in rows if row["route"] == route
                        and row["profile"] == profile]
            summaries.append({"route": route, "profile": profile,
                "opportunities": len(selected),
                "baseline_three_streets": sum(row["baseline"]["three_streets"]
                                              for row in selected),
                "made_counts": dict(Counter(row["made"] for row in selected)),
                "mean_net_bb": {choice: (statistics.mean(
                    row["alternatives"][choice]["net_bb"] for row in selected)
                    if selected else None) for choice in choices},
                "mean_vs_baseline_bb": {choice: (statistics.mean(
                    row["alternatives"][choice]["net_bb"] -
                    row["baseline"]["net_bb"] for row in selected)
                    if selected else None) for choice in choices},
                "complete_route_by_action": {choice: sum(
                    row["alternatives"][choice]["three_streets"] for row in selected)
                    for choice in choices}})
    receipt = {"version": "1.9.0", "seeds": seeds, "profiles": PROFILES,
               "rules_source": "assumed_for_research", "coverage_nodes": dict(coverage),
               "summaries": summaries, "rows": rows,
               "limitations": "Scripted opponent and fixed 100BB; flop intervention only, later streets use baseline. Conditional on reached prefix; no solver/range truth."}
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(receipt, separators=(",", ":")) + "\n", encoding="utf-8")
    return {"rows": len(rows), "summaries": summaries}


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    print(json.dumps(run(args.output)))
