"""A versioned view of the frozen heuristic chart and its executable sizes."""
from collections import defaultdict
from dataclasses import dataclass
from itertools import combinations

from .poker import DECK, hand_class
from .preflop import TABLE_SHA256, lookup
from .strategy import legalize


@dataclass(frozen=True)
class RuleSpec:
    player_count: int = 2
    starting_stack_bb: int = 100
    ante: int = 0
    small_blind: int = 5
    big_blind: int = 10
    rake: int = 0
    open_sizes_bb: tuple[float, ...] = (2.0, 2.25, 2.5)
    iso_sizes_bb: tuple[float, ...] = (3.0, 4.0, 5.0)
    three_bet_multipliers: tuple[float, ...] = (3.0, 3.5, 4.0)
    source: str = "assumed_for_research"


def executable_policy(base, state):
    """Collapse labels that clamp to the same SDK action and raise-to total."""
    merged = defaultdict(float)
    for label, probability in base.normalized().weights.items():
        action, _ = legalize(label, state)
        merged[(action.action, int(getattr(action, "amount", 0) or 0))] += probability
    return dict(merged)


class PreflopBlueprint:
    table_sha256 = TABLE_SHA256
    source = "original-authored-heuristic; not solver-derived"

    def policy(self, state):
        from .preflop import preflop_policy
        return preflop_policy(state).normalized()

    def legal_policy(self, state):
        return executable_policy(self.policy(state), state)

    def bb_response_prior(self, open_to, big_blind):
        """Uniform 1326-combo approximation; no opponent range or blockers fitted."""
        totals = {"fold": 0.0, "call": 0.0, "raise": 0.0}
        for cards in combinations(DECK, 2):
            weights = lookup("BB_OPEN", 100, open_to/big_blind, hand_class(cards)).weights
            totals["fold"] += weights.get("FOLD", 0)
            totals["call"] += weights.get("CALL", 0)
            totals["raise"] += sum(v for k, v in weights.items()
                                   if k.startswith("RAISE_") or k == "ALL_IN")
        return {key: value/1326 for key, value in totals.items()}


def small_tree_open_ev(fold_rate, open_to_bb=2.25, big_blind=1.0,
                       continuation_loss_bb=None):
    """Diagnostic one-decision toy tree, not a four-street action EV.

    A fold wins the BB; continuation is a configurable net loss. The default
    loss of the full opening amount is an assumption, not a lower bound on
    postflop risk. Callers must specify loss for sensitivity analysis.
    """
    if not 0 <= fold_rate <= 1 or open_to_bb <= 0 or big_blind <= 0:
        raise ValueError("Invalid tree inputs")
    loss = open_to_bb if continuation_loss_bb is None else continuation_loss_bb
    if loss < 0:
        raise ValueError("Loss must be nonnegative")
    return fold_rate*big_blind-(1-fold_rate)*loss
