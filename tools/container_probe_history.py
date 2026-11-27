"""Exercise the inherited price guard in the packaged, restricted image."""
from dataclasses import replace
import json

from chipzen import GameState
from chipzen.models import Card

import hu_bot
from hu_bot.config import Config
from hu_bot.core import normalize
from hu_bot.engine import Engine
from hu_bot.range_spec import candidate_status


def event(seat, action, amount):
    return {"seat": seat, "action": action, "amount": amount,
            "phase": "preflop", "is_timeout": False}


def spot(context, depth, price=0, cards=("Jc", "3c"), bb=100):
    sb = bb//2
    events = [event(0, "post_small_blind", sb), event(1, "post_big_blind", bb)]
    bets = [sb, bb]
    if context == "BB_VS_OPEN":
        bets[0] = round(price*bb)
        events.append(event(0, "raise", bets[0]))
    elif context == "BB_VS_LIMP":
        bets[0] = bb
        events.append(event(0, "call", sb))
    elif context != "BTN_FIRST_IN":
        raise ValueError(context)
    seat = 0 if context == "BTN_FIRST_IN" else 1
    owed = max(0, bets[1-seat]-bets[seat])
    chips = depth*bb
    return GameState(hand_number=1, phase="preflop",
                     hole_cards=[Card.from_str(card) for card in cards], board=[],
                     pot=sum(bets), your_stack=chips-bets[seat],
                     opponent_stacks=[chips-bets[1-seat]], your_seat=seat,
                     dealer_seat=0, to_call=owed,
                     min_raise=max(bets)+bb, max_raise=chips,
                     valid_actions=["fold", "call", "raise"] if owed else
                                   ["check", "raise"], action_history=events)


def probe(raw, expected):
    state = normalize(raw, 100)
    _, status = candidate_status(state)
    assert status == expected, status
    settings = Config(seed=983491, trace=False, retain_diagnostics=True,
                      learn_opponent=False, adapt_ranges=False, adapt_responses=False)
    candidate = Engine(settings)
    baseline = Engine(replace(settings, range_repair_enabled=False))
    candidate.reset("image-history-gate", 100)
    baseline.reset("image-history-gate", 100)
    action, old = candidate.decide(raw), baseline.decide(raw)
    trace, control = candidate.last_trace, baseline.last_trace
    assert not trace["fallback"] and not control["fallback"]
    assert action.action in raw.valid_actions
    assert trace["preflop_range"]["status"] == expected
    if expected != "applied":
        assert action.to_dict() == old.to_dict()
        assert trace["policy"] == control["policy"]
        assert not trace["preflop_range"]["applied"]
    assert trace["elapsed_ms"] < 450
    return {"status": status, "action": action.to_dict(),
            "old_action": old.to_dict(), "elapsed_ms": trace["elapsed_ms"]}


def main():
    assert hu_bot.__version__ == "1.9.2"
    assert hu_bot.__file__.startswith("/bot/"), hu_bot.__file__
    cases = {}
    for name, context, depth, price in (
            ("btn_first_in", "BTN_FIRST_IN", 60, 0),
            ("bb_limp", "BB_VS_LIMP", 100, 1),
            ("bb_open_2", "BB_VS_OPEN", 60, 2),
            ("bb_open_225", "BB_VS_OPEN", 60, 2.25)):
        cases[name] = probe(spot(context, depth, price), "applied")
    raw = spot("BB_VS_OPEN", 60, 2)
    stale = replace(raw, pot=1100, to_call=900, min_raise=1900,
                    opponent_stacks=[5000])
    cases["stale_2bb_history_facing_10bb"] = probe(stale, "history_price_mismatch")
    raw = spot("BTN_FIRST_IN", 60)
    stale = replace(raw, pot=raw.pot+950, to_call=1000, min_raise=2000,
                    opponent_stacks=[raw.opponent_stacks[0]-1000])
    cases["missing_btn_raise"] = probe(stale, "history_price_mismatch")
    cases["100bb_bb_open_2_out_of_scope"] = probe(
        spot("BB_VS_OPEN", 100, 2), "out_of_scope")
    raw = spot("BB_VS_OPEN", 60, 2.25)
    cases["pot_history_conflict"] = probe(
        replace(raw, pot=raw.pot+100), "history_price_mismatch")
    cases["60bb_bb_open_25_research_off"] = probe(
        spot("BB_VS_OPEN", 60, 2.5), "out_of_scope")
    print(json.dumps({"version": hu_bot.__version__, "import": hu_bot.__file__,
                      "cases": cases}))


if __name__ == "__main__":
    main()
