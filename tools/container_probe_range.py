"""Restricted-container probe for all supported range nodes and both depths."""
from dataclasses import replace
import json

from chipzen import GameState
from chipzen.models import Card

from hu_bot import __version__
from hu_bot.config import Config
from hu_bot.engine import Engine
from hu_bot.preflop import TABLE_SHA256
from hu_bot.range_spec import candidate_policy
from hu_bot.core import normalize


def event(seat, action, amount):
    return {"seat": seat, "action": action, "amount": amount,
            "phase": "preflop", "is_timeout": False}


def state(context, depth, price):
    events = [event(0, "post_small_blind", 5), event(1, "post_big_blind", 10)]
    bets = [5, 10]
    if context == "BB_VS_LIMP":
        events.append(event(0, "call", 5))
        bets[0] = 10
    elif context == "BB_VS_OPEN":
        amount = round(price*10)
        events.append(event(0, "raise", amount))
        bets[0] = amount
    elif context == "BTN_VS_ISO":
        amount = round(price*10)
        events += [event(0, "call", 5), event(1, "raise", amount)]
        bets = [10, amount]
    elif context in {"BTN_VS_3BET", "BB_VS_4BET"}:
        opened = round(price*10)
        three = round(opened*3.5)
        events += [event(0, "raise", opened), event(1, "raise", three)]
        bets = [opened, three]
        if context == "BB_VS_4BET":
            four = round(three*2.25)
            events.append(event(0, "raise", four))
            bets[0] = four
    elif context == "BB_VS_LIMP_RERAISE":
        four = round(35*2.25)
        events += [event(0, "call", 5), event(1, "raise", 35),
                   event(0, "raise", four)]
        bets = [four, 35]
    elif context != "BTN_FIRST_IN":
        raise ValueError(context)
    seat = 0 if context.startswith("BTN") else 1
    owed = max(0, bets[1-seat]-bets[seat])
    return GameState(hand_number=1, phase="preflop",
        hole_cards=[Card.from_str("Qh"), Card.from_str("8d")], board=[],
        pot=sum(bets), your_stack=depth*10-bets[seat],
        opponent_stacks=[depth*10-bets[1-seat]], your_seat=seat, dealer_seat=0,
        to_call=owed, min_raise=max(bets)+10, max_raise=depth*10,
        valid_actions=["fold", "call", "raise"] if owed else ["check", "raise"],
        action_history=events)


def main():
    cases = {"BTN_FIRST_IN": [0], "BB_VS_LIMP": [1],
             "BB_VS_OPEN": [2, 2.25, 2.5, 3],
             "BTN_VS_ISO": [3.5, 5], "BTN_VS_3BET": [2, 3],
             "BB_VS_4BET": [2, 3], "BB_VS_LIMP_RERAISE": [3.5]}
    receipt = {"version": __version__, "old_chart_sha256": TABLE_SHA256,
               "limit": "0.5 CPU, 256 MiB, read-only, non-root",
               "cases": []}
    for context, prices in cases.items():
        for depth in (100, 60):
            for price in prices:
                raw = state(context, depth, price)
                active = (context == "BTN_FIRST_IN" and depth == 60 or
                          context == "BB_VS_LIMP" and depth == 100 or
                          context == "BB_VS_OPEN" and depth == 60 and price <= 2.25)
                assert (candidate_policy(normalize(raw, 10)) is not None) == active
                for enabled in (False, True):
                    config = replace(Config(trace=False, retain_diagnostics=True,
                        learn_opponent=False, adapt_ranges=False,
                        adapt_responses=False, exploit_cap=0.0),
                        range_repair_enabled=enabled,
                        preflop_overfold_enabled=False)
                    engine = Engine(config)
                    action = engine.evaluate(raw)
                    trace = engine.last_trace
                    assert action.action in raw.valid_actions
                    assert trace["elapsed_ms"] < 450
                    assert not trace["fallback"]
                    assert not trace["preflop_overfold"].get("applied")
                    assert trace["preflop_range"]["applied"] == (enabled and active)
                    receipt["cases"].append({"context": context, "depth_bb": depth,
                        "nominal_price_bb": price, "candidate": enabled,
                        "active": active, "policy_l1": trace["preflop_range"]["policy_l1"],
                        "action": action.to_dict(), "elapsed_ms": trace["elapsed_ms"]})
    for context, depth, price, active in (
        ("BTN_FIRST_IN", 50, 0, True), ("BTN_FIRST_IN", 70, 0, True),
        ("BB_VS_LIMP", 90, 1, True), ("BB_VS_LIMP", 80, 1, False),
        ("BB_VS_OPEN", 50, 2, True), ("BB_VS_OPEN", 70, 2, True),
        ("BB_VS_OPEN", 55, 2.25, True), ("BB_VS_OPEN", 65, 2.25, True),
        ("BB_VS_OPEN", 50, 2.25, False), ("BB_VS_OPEN", 70, 2.25, False),
    ):
        raw = state(context, depth, price)
        assert (candidate_policy(normalize(raw, 10)) is not None) == active
        engine = Engine(Config(trace=False))
        action = engine.evaluate(raw)
        trace = engine.last_trace
        assert action.action in raw.valid_actions and not trace["fallback"]
        assert trace["preflop_range"]["applied"] == active
        receipt["cases"].append({"context": context, "depth_bb": depth,
                                 "nominal_price_bb": price, "active": active,
                                 "policy_l1": trace["preflop_range"]["policy_l1"],
                                 "action": action.to_dict(),
                                 "elapsed_ms": trace["elapsed_ms"]})
    for context, current_price in (("BTN_VS_3BET", 300), ("BB_VS_4BET", 500)):
        raw = state(context, 100, 2)
        events = [dict(e) for e in raw.action_history]
        old_price = events[-1]["amount"]
        events[-1]["amount"] = current_price
        raw = replace(raw, action_history=events, pot=raw.pot+current_price-old_price,
                      to_call=raw.to_call+current_price-old_price,
                      opponent_stacks=[raw.opponent_stacks[0]-(current_price-old_price)],
                      min_raise=min(1000, current_price+current_price-old_price))
        assert candidate_policy(normalize(raw, 10)) is None
        engine = Engine(Config(trace=False))
        action = engine.evaluate(raw)
        assert action.action in raw.valid_actions and not engine.last_trace["fallback"]
        assert not engine.last_trace["preflop_range"]["applied"]
        receipt["cases"].append({"context": context, "current_raise_to": current_price,
                                 "active": False, "action": action.to_dict(),
                                 "elapsed_ms": engine.last_trace["elapsed_ms"]})
    print(json.dumps(receipt))


if __name__ == "__main__":
    main()
