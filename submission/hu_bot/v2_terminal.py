"""Standard heads-up, no-fee terminal cashflow oracle for offline comparisons.

This module is deliberately independent of the online policy.  It resolves
current decision-point chip cashflow for fold, win/loss, tie, odd chips and an
uncalled wager.  Platform-specific return rules remain unverified metadata.
"""

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class TerminalResult:
    outcome: str
    contestable_pot: int
    returned_uncalled: int
    payouts: tuple[int, int]
    final_stacks: tuple[int, int]
    q_chip: int
    conserved: bool
    status: str = "standard_hu_no_fee_oracle"
    platform_return_verified: bool = False


def resolve_terminal(current_stacks, committed, outcome, *, hero_seat=0,
                     folded_seat=None, uncalled_seat=None, uncalled_amount=0,
                     odd_chip_seat=0, displayed_pot=None):
    """Resolve a terminal hand from the current decision point.

    ``current_stacks`` excludes the amounts in ``committed``.  ``committed``
    is the total contribution by seat, and ``uncalled_amount`` is returned to
    ``uncalled_seat`` before contestable-pot distribution.  ``q_chip`` is the
    hero's final stack change from this decision point.
    """
    stacks = tuple(int(value) for value in current_stacks)
    bets = tuple(int(value) for value in committed)
    if len(stacks) != 2 or len(bets) != 2 or any(value < 0 for value in stacks + bets):
        raise ValueError("two non-negative seat values are required")
    if outcome not in {"fold", "hero_win", "villain_win", "tie"}:
        raise ValueError("unsupported terminal outcome")
    if hero_seat not in (0, 1) or odd_chip_seat not in (0, 1):
        raise ValueError("seat must be 0 or 1")
    if displayed_pot is not None and int(displayed_pot) != sum(bets):
        raise ValueError("displayed pot does not match committed contributions")
    returned = int(uncalled_amount)
    if returned < 0 or returned > sum(bets):
        raise ValueError("invalid uncalled amount")
    if returned and uncalled_seat not in (0, 1):
        raise ValueError("uncalled seat is required for a returned wager")
    if returned and returned > bets[uncalled_seat]:
        raise ValueError("uncalled wager exceeds seat contribution")
    contestable = sum(bets) - returned
    payouts = [0, 0]
    if outcome == "fold":
        if folded_seat not in (0, 1):
            raise ValueError("folded seat is required for fold outcome")
        payouts[1 - folded_seat] = contestable
    elif outcome == "hero_win":
        payouts[hero_seat] = contestable
    elif outcome == "villain_win":
        payouts[1 - hero_seat] = contestable
    else:
        payouts[0] = contestable // 2
        payouts[1] = contestable // 2
        if contestable % 2:
            payouts[odd_chip_seat] += 1
    final = list(stacks)
    if returned:
        final[uncalled_seat] += returned
    final[0] += payouts[0]
    final[1] += payouts[1]
    conserved = sum(final) == sum(stacks) + sum(bets)
    if not conserved:
        raise AssertionError("terminal cashflow is not conserved")
    return TerminalResult(outcome, contestable, returned, tuple(payouts),
                          tuple(final), final[hero_seat] - stacks[hero_seat], conserved)
