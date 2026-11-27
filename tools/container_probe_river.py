"""Restricted-image normal-entry probe for a complete legal river prefix."""

import json

from chipzen import GameState
from chipzen.models import Card

from hu_bot import __version__
from hu_bot.config import Config
from hu_bot.engine import Engine


def event(seat, action, amount, phase):
    return {"seat": seat, "action": action, "amount": amount,
            "phase": phase, "is_timeout": False}


PREFIX = [
    event(0, "post_small_blind", 50, "preflop"),
    event(1, "post_big_blind", 100, "preflop"),
    event(0, "raise", 260, "preflop"),
    event(1, "call", 160, "preflop"),
    event(1, "check", 0, "flop"),
    event(0, "raise", 436, "flop"),
    event(1, "call", 436, "flop"),
    event(1, "check", 0, "turn"),
    event(0, "raise", 835, "turn"),
    event(1, "raise", 3131, "turn"),
    event(0, "call", 2296, "turn"),
]


def state(hole, facing):
    history = PREFIX.copy()
    if facing:
        history += [event(1, "raise", 5052, "river"),
                    event(0, "raise", 5207, "river")]
    return GameState(hand_number=1, phase="river",
        hole_cards=[Card.from_str(c) for c in hole],
        board=[Card.from_str(c) for c in ("Ah", "Js", "4s", "As", "4h")],
        pot=17913 if facing else 7654,
        your_stack=155 if facing else 5207,
        opponent_stacks=[1662 if facing else 6869],
        your_seat=1, dealer_seat=0, to_call=155 if facing else 0,
        min_raise=0 if facing else 100,
        max_raise=0 if facing else 5207,
        valid_actions=["fold", "call"] if facing else ["check", "raise"],
        action_history=history)


def main():
    assert __version__ == "1.9.2"
    rows = []
    for hole in (("Qs", "5s"), ("Ks", "Ts"), ("9s", "7s"), ("6s", "2s")):
        for facing in (False, True):
            raw = state(hole, facing)
            engine = Engine(Config(trace=False))
            action = engine.evaluate(raw)
            trace = engine.last_trace
            assert action.action in raw.valid_actions
            assert not trace["fallback"]
            assert trace["elapsed_ms"] < 450
            rows.append({"hole": hole, "facing_effective_raise": facing,
                         "action": action.to_dict(),
                         "elapsed_ms": trace["elapsed_ms"]})
    print(json.dumps({"version": __version__, "limits":
        "0.5 CPU, 256 MiB, 450 ms, read-only, non-root, no network",
        "rows": rows}))


if __name__ == "__main__":
    main()
