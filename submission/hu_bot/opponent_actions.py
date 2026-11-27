"""Bounded, opportunity-based postflop action and sizing observations."""
from dataclasses import dataclass, field

from .responses import size_bucket


MAX_ACTION_CELLS = 128
_POT_TYPES = ("LIMP", "SRP", "3BET", "4BET")
_SIZE_BUCKETS = ("small", "medium", "pot", "over")


@dataclass
class ActionCell:
    """One decision context; counts share one mutually exclusive denominator."""

    counts: dict[str, int] = field(default_factory=dict)
    size_counts: dict[str, int] = field(default_factory=dict)

    @property
    def opportunities(self):
        return sum(self.counts.values())

    def observe(self, action, size=None):
        self.counts[action] = self.counts.get(action, 0) + 1
        if size is not None:
            self.size_counts[size] = self.size_counts.get(size, 0) + 1


def action_observation(event, hero_seat, dealer_seat):
    """Classify a villain decision using only facts known before that action."""
    if (event.seat == hero_seat or event.is_timeout or
            event.street not in {"flop", "turn", "river"}):
        return None
    n = event.pf_raises_before
    pot_type = _POT_TYPES[min(n, len(_POT_TYPES) - 1)]
    position = "BTN" if event.seat == dealer_seat else "BB"
    action = event.action
    aggressive = action == "raise" or (action == "all_in" and
                                         event.amount_to > event.table_bet)
    called = action == "call" or (action == "all_in" and
                                  event.amount_to <= event.table_bet)
    if event.to_call == 0 and event.street_raises_before == 0:
        if action == "check":
            choice = "check"
        elif aggressive:
            choice = "bet"
        else:
            return None
        key = (event.street, pot_type, position, "unopened", None)
    elif event.to_call > 0 and event.street_raises_before > 0:
        if action == "fold":
            choice = "fold"
        elif called:
            choice = "call"
        elif aggressive:
            choice = "raise"
        else:
            return None
        spot = "facing_first_bet" if event.street_raises_before == 1 else "facing_raise"
        # The current pot includes the wager being faced, which must be removed.
        price = event.to_call / max(1, event.pot_before - event.to_call)
        key = (event.street, pot_type, position, spot, size_bucket(price))
    else:
        return None
    size = size_bucket(event.size_ratio) if aggressive else None
    assert size is None or size in _SIZE_BUCKETS
    return key, choice, size


def observe_action(model, event, hero_seat, dealer_seat):
    observation = action_observation(event, hero_seat, dealer_seat)
    if observation is None:
        return
    key, choice, size = observation
    cell = model.action_cells.get(key)
    if cell is None:
        if len(model.action_cells) >= model.action_cell_limit:
            return
        cell = model.action_cells[key] = ActionCell()
    cell.observe(choice, size)
