"""One-pass HU betting ledger shared by range inference and opportunity counters."""
from dataclasses import dataclass, replace


@dataclass(frozen=True)
class ActionContext:
    index: int
    seat: int
    action: str
    street: str
    pot_before: int
    own_bet: int
    table_bet: int
    to_call: int
    amount_to: int
    contribution: int
    size_ratio: float
    call_odds: float
    pf_raises_before: int
    street_raises_before: int
    previous_aggressor: int | None
    effective_depth_bb: float = 0.0
    is_jam: bool = False
    facing_jam: bool = False
    is_timeout: bool = False


def ledger(history, big_blind=10, current_stacks=None):
    """Calls match the table bet; antes are dead money, raises are street totals.

    Starting stacks are reconstructed from CURRENT remaining stacks plus each
    seat's total committed chips, never from the pot paid out at hand end.
    current_stacks=None is useful for opportunity counting, without guessing depth.
    """
    pot, bets, committed = 0, {}, {}
    street, pf_raises, street_raises, aggressor = "preflop", 0, 0, None
    events, before_committed = [], []
    for i, e in enumerate(history):
        phase = e.get("phase", "preflop")
        if phase != street:
            street, bets, street_raises, aggressor = phase, {}, 0, None
        seat, action, amount = int(e["seat"]), e["action"], int(e.get("amount", 0))
        own, table = bets.get(seat, 0), max(bets.values(), default=0)
        owed = max(0, table-own)
        if action == "post_ante":
            contribution, target = amount, own
        elif action in {"raise", "all_in", "post_small_blind", "post_big_blind"}:
            contribution, target = max(0, amount-own), max(own, amount)
        elif action == "call":
            contribution, target = owed, table
        else:
            contribution, target = 0, own
        ratio = (owed/max(1, pot-owed) if action == "call" else
                 max(0, target-table)/max(1, pot+owed))
        events.append(ActionContext(i, seat, action, street, pot, own, table, owed,
            target, contribution, ratio, owed/max(1, pot+owed), pf_raises,
            street_raises, aggressor, is_timeout=bool(e.get("is_timeout", False))))
        before_committed.append(dict(committed))
        committed[seat] = committed.get(seat, 0)+contribution
        pot += contribution
        bets[seat] = target
        if action in {"raise", "all_in"} and target > table:
            street_raises += 1
            pf_raises += street == "preflop"
            aggressor = seat
    if current_stacks is not None:
        initial = {seat: stack+committed.get(seat, 0) for seat, stack in current_stacks.items()}
        for i, event in enumerate(events):
            # The preflop policy uses depth at the start of that betting street,
            # so include each seat's live bet but exclude previously dead money.
            paid = before_committed[i]
            own_remaining = initial[event.seat]-paid.get(event.seat, 0)
            other = 1-event.seat
            other_remaining = initial[other]-paid.get(other, 0)
            depth = min(own_remaining+event.own_bet, other_remaining+event.table_bet)/big_blind
            events[i] = replace(event, effective_depth_bb=depth,
                is_jam=event.action in {"raise", "all_in"} and
                    event.contribution >= min(own_remaining, other_remaining+event.to_call),
                facing_jam=event.to_call > 0 and (other_remaining <= 0 or event.to_call >= own_remaining))
    return events


def preflop_context(event, dealer_seat, big_blind):
    """Classify the decision BEFORE the observed action, avoiding future-action leakage."""
    if event.facing_jam:
        return "VS_JAM", 2.0
    n = event.pf_raises_before
    if n == 0:
        return ("BTN_OPEN" if event.seat == dealer_seat else "BB_LIMP"), 2.0
    if n == 1:
        return ("BTN_ISOLATION" if event.seat == dealer_seat else "BB_OPEN"), event.table_bet/big_blind
    return ("VS_3BET" if n == 2 else "VS_4BET"), event.table_bet/max(1, event.own_bet)
