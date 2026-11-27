"""Independent, read-only v1.10.0 acceptance probe.

The script is intentionally self-contained so the same inputs can be run in
the v1.10.0 and v1.9.2 images.  It writes no bot state and only emits JSON.
"""

from __future__ import annotations

import argparse
import json
from dataclasses import asdict

from chipzen import GameState
from chipzen.models import Card


def event(seat, action, amount=0, phase="preflop"):
    return {"seat": seat, "action": action, "amount": amount,
            "phase": phase, "is_timeout": False}


def state(*, hole="Ah Kd", board="", pot=15, your_stack=995,
          opponent_stacks=(990,), your_seat=0, dealer_seat=0, to_call=5,
          min_raise=20, max_raise=1000, valid_actions=None,
          action_history=None, hand_number=1, big_blind=10, **extra):
    cards = board.split()
    if valid_actions is None:
        valid_actions = ["fold", "call", "raise"] if to_call else ["check", "raise"]
    if action_history is None:
        action_history = [event(0, "post_small_blind", big_blind // 2),
                          event(1, "post_big_blind", big_blind)]
    values = dict(
        hand_number=hand_number, phase={0: "preflop", 3: "flop", 4: "turn", 5: "river"}[len(cards)],
        hole_cards=[Card.from_str(c) for c in hole.split()],
        board=[Card.from_str(c) for c in cards], pot=pot, your_stack=your_stack,
        opponent_stacks=list(opponent_stacks), your_seat=your_seat,
        dealer_seat=dealer_seat, to_call=to_call, min_raise=min_raise,
        max_raise=max_raise, valid_actions=list(valid_actions),
        action_history=list(action_history),
    )
    values.update(extra)
    return GameState(**values)


def cases():
    blinds = [event(0, "post_small_blind", 5), event(1, "post_big_blind", 10)]
    open_2bb = blinds + [event(0, "raise", 20)]
    river_bet = blinds + [event(0, "raise", 25), event(1, "call", 15),
                          event(0, "check", 0, "flop"), event(1, "check", 0, "flop"),
                          event(0, "check", 0, "turn"), event(1, "check", 0, "turn"),
                          event(1, "raise", 100, "river")]
    return {
        "valid_preflop": state(hole="Qh 8d", pot=30, your_stack=990,
            opponent_stacks=(980,), your_seat=1, to_call=10, min_raise=30,
            action_history=open_2bb),
        "history_2bb_authority_10bb": state(hole="Jc 3c", pot=110,
            your_stack=900, opponent_stacks=(800,), to_call=90, min_raise=190,
            action_history=open_2bb),
        "pot_conflict": state(hole="Qh 8d", pot=130, your_stack=990,
            opponent_stacks=(980,), to_call=10, min_raise=30,
            action_history=open_2bb),
        "invalid_raise_bounds": state(hole="Ah Ad", pot=15, your_stack=995,
            opponent_stacks=(990,), to_call=5, min_raise=80, max_raise=20,
            action_history=blinds),
        "unequal_allin_unknown_refund": state(hole="As Ah", pot=60,
            your_stack=0, opponent_stacks=(50,), to_call=50, min_raise=50,
            max_raise=50, valid_actions=["fold", "call", "raise"],
            action_history=blinds + [event(0, "all_in", 50)]),
        "heavy_history_720": state(hole="Ah 7d", board="As 8c 4d 2h 9s",
            pot=150, your_stack=875, opponent_stacks=(875,), to_call=100,
            min_raise=200, max_raise=875,
            valid_actions=["fold", "call", "raise"], action_history=river_bet +
                [event(i % 2, "check", 0, "river") for i in range(720)],
            hand_number=720),
        "paired_river_sentinel": state(hole="Qs 5s", board="Ah Js 4s As 4h",
            pot=50, your_stack=975, opponent_stacks=(975,), to_call=0,
            min_raise=10, max_raise=975, valid_actions=["check", "raise"],
            action_history=blinds + [event(0, "raise", 25), event(1, "call", 15),
                                     event(0, "check", 0, "flop"), event(1, "check", 0, "flop"),
                                     event(0, "check", 0, "turn"), event(1, "check", 0, "turn")]),
        "river_large_active_bet": state(hole="Ah 7d", board="As 8c 4d 2h 9s",
            pot=200, your_stack=825, opponent_stacks=(875,), to_call=150,
            min_raise=300, max_raise=875, valid_actions=["fold", "call", "raise"],
            action_history=blinds + [event(0, "raise", 25), event(1, "call", 15),
                                     event(0, "check", 0, "flop"), event(1, "check", 0, "flop"),
                                     event(0, "check", 0, "turn"), event(1, "check", 0, "turn"),
                                     event(1, "raise", 150, "river")]),
        "river_prior_scope_probe": state(hole="Ah 7d", board="As 8c 4d 2h 9s",
            pot=160, your_stack=855, opponent_stacks=(875,), your_seat=1,
            to_call=120, min_raise=240, max_raise=875,
            valid_actions=["fold", "call", "raise"],
            action_history=blinds + [event(0, "raise", 20), event(1, "call", 10),
                                     event(0, "check", 0, "flop"), event(1, "check", 0, "flop"),
                                     event(0, "check", 0, "turn"), event(1, "check", 0, "turn"),
                                     event(0, "raise", 120, "river")]),
        "blind_100bb": state(hole="Qh 8d", pot=300, your_stack=9700,
            opponent_stacks=(9600,), to_call=100, min_raise=300, max_raise=10000,
            action_history=[event(0, "post_small_blind", 50),
                            event(1, "post_big_blind", 100), event(0, "raise", 200)]),
    }


def run():
    from hu_bot.config import Config
    from hu_bot.engine import Engine
    from hu_bot import __version__

    engine = Engine(Config(seed=20261012, trace=False, retain_diagnostics=True,
                           max_samples=128, decision_budget_ms=450))
    engine.reset("independent-v110", 10, 900)
    rows = []
    for name, raw in cases().items():
        action = engine.decide(raw)
        trace = engine.last_trace
        strength = trace.get("strength_slice", {})
        rows.append({
            "case": name,
            "version": __version__,
            "action": action.to_dict(),
            "fallback": bool(trace.get("fallback")),
            "accounting": trace.get("accounting", {}),
            "preflop_range": trace.get("preflop_range", {}),
            "last_aggressor": trace.get("last_aggressor"),
            "strength": {
                "node_key": strength.get("node_key"),
                "action_value": strength.get("action_value"),
                "low_bluff_prior_scope": strength.get("low_bluff_prior_scope"),
                "strategy_gate": strength.get("strategy_gate"),
                "resource": strength.get("resource"),
            },
            "elapsed_ms": trace.get("elapsed_ms"),
            "policy": trace.get("policy"),
        })
    return {"version": __version__, "rows": rows,
            "counters": dict(engine.match_counters)}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    with open(args.output, "w", encoding="utf-8") as handle:
        json.dump(run(), handle, indent=2, sort_keys=True)
    print(args.output)
