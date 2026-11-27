"""Run the cash-flow counterexamples inside the release image."""
import json

from hu_bot.accounting import (RawActionEvent, RuleSnapshot, build_ledger,
                               settlement_ledger)
from hu_bot import __version__


def event(index, seat, action, amount):
    return RawActionEvent(index, seat, action, "preflop", amount, False,
                          (("seat", seat), ("action", action), ("amount", amount)))


def main():
    receipts = []
    for stack in (500, 800):
        history = (event(0, 0, "post_small_blind", 50),
                   event(1, 1, "post_big_blind", 100),
                   event(2, 0, "raise", stack),
                   event(3, 1, "call", stack-100))
        ledger = build_ledger(history, current_street="preflop",
                              authoritative_pot=2*stack, authoritative_to_call=0,
                              hero_seat=0, hand_start_stacks=(stack, stack),
                              current_stacks=(0, 0), seat_count=2)
        assert ledger.consistent and ledger.potential_uncalled == 0
        receipts.append({"case": f"symmetric_{stack//100}bb", "contestable": ledger.contestable_pot,
                         "refund": ledger.potential_uncalled,
                         "raise_to": ledger.events[2].raise_to})
    history = (event(0, 0, "post_small_blind", 50),
               event(1, 1, "post_big_blind", 100),
               event(2, 0, "raise", 800), event(3, 1, "call", 400))
    ledger = build_ledger(history, current_street="preflop", authoritative_pot=1300,
                          authoritative_to_call=0, hero_seat=0,
                          hand_start_stacks=(800, 500), current_stacks=(0, 0), seat_count=2)
    assert ledger.committed_by_seat == (800, 500)
    assert ledger.contestable_pot == 1000 and ledger.potential_uncalled == 300
    settled = settlement_ledger(ledger, {"uncalled_refunds": [300, 0]})
    assert settled.refunds == ((0, 300),)
    receipts.append({"case": "8bb_vs_5bb", "contestable": ledger.contestable_pot,
                     "refund": ledger.potential_uncalled, "settled_refund": settled.refunds})
    conflict = build_ledger((event(0, 0, "post_small_blind", 50),
                             event(1, 1, "post_big_blind", 100)),
                            current_street="preflop", authoritative_pot=999,
                            authoritative_to_call=20, hero_seat=0,
                            hand_start_stacks=(500, 500), current_stacks=(450, 400), seat_count=2)
    assert not conflict.consistent and "pot_history_gap:150->999" in conflict.conflicts
    receipts.append({"case": "history_authority_conflict", "status": conflict.status,
                     "conflicts": conflict.conflicts})
    old = RuleSnapshot.from_config("m", {"big_blind": 100})
    new = old.with_observed_big_blind(200)
    assert old.chips_to_bb(200) == 2.0 and new.chips_to_bb(200) == 1.0
    receipts.append({"case": "blind_change", "old_bb": old.big_blind,
                     "new_bb": new.big_blind, "two_hundred_old_bb": old.chips_to_bb(200),
                     "two_hundred_new_bb": new.chips_to_bb(200)})
    print(json.dumps({"version": __version__, "cases": receipts}))


if __name__ == "__main__":
    main()
