"""Conservative, opt-in terminal call/fold candidate.

This is intentionally isolated from the production engine.  Its contract is
strict: ``state.pot`` is the pot at the decision point and therefore already
contains the opponent's facing bet.  It only intervenes after it can confirm a
heads-up, no-future-betting showdown from the normalized state and its action
history.
"""
from __future__ import annotations

from math import floor, isfinite
from typing import Any

from hu_bot.core import Policy, street_bets


def public_royal_flush(board: tuple[str, ...] | list[str]) -> bool:
    """True only for a five-card public royal flush, never for generic board play."""
    if len(board) != 5:
        return False
    cards = tuple(map(str, board))
    if any(len(card) != 2 for card in cards):
        return False
    return len(set(cards)) == 5 and {card[0] for card in cards} == set("TJQKA") and \
        len({card[1] for card in cards}) == 1 and cards[0][1] in "cdhs"


def guaranteed_royal_call(state: Any) -> tuple[Policy | None, dict[str, Any]]:
    """Return CALL only when a public river royal makes calling strictly profitable.

    A board royal is an unbeatable shared hand in Hold'em, so no range or
    equity estimate is involved.  A non-royal public flush (including 6-high)
    is deliberately ignored because a private card can improve it.
    """
    needed = ("num_players", "board", "street", "pot", "to_call", "hero_bet",
              "villain_bet", "hero_seat", "action_history", "valid_actions", "hero_stack")
    absent = [name for name in needed if not hasattr(state, name)]
    if absent:
        return _unsupported("missing_state_fields", missing=absent)
    if state.num_players != 2:
        return _unsupported("not_heads_up", num_players=state.num_players)
    if state.street != "river" or not public_royal_flush(state.board):
        return _unsupported("not_public_river_royal_flush")
    if not isinstance(state.valid_actions, (tuple, list, set)):
        return _unsupported("invalid_valid_actions")
    if "call" not in state.valid_actions or "fold" not in state.valid_actions:
        return _unsupported("call_or_fold_not_legal", valid_actions=tuple(state.valid_actions))
    pot, call = _number(state.pot, "pot"), _number(state.to_call, "to_call")
    hero_bet, villain_bet = _number(state.hero_bet, "hero_bet"), _number(state.villain_bet, "villain_bet")
    if None in (pot, call, hero_bet, villain_bet) or min(pot, call, hero_bet, villain_bet) < 0:
        return _unsupported("invalid_chip_value")
    if call <= 0:
        return _unsupported("no_facing_bet")
    if _number(state.hero_stack, "hero_stack") is None or call > state.hero_stack:
        return _unsupported("call_exceeds_available_stack")
    if villain_bet - hero_bet != call:
        return _unsupported("uncalled_or_noncanonical_bet_gap",
                            observed_gap=villain_bet - hero_bet, to_call=call)
    if not state.action_history:
        return _unsupported("history_cannot_confirm_contestable_pot")
    try:
        observed = street_bets(tuple(state.action_history), state.street)
    except (KeyError, TypeError, ValueError):
        return _unsupported("history_cannot_confirm_contestable_pot")
    hero_observed = float(observed.get(state.hero_seat, 0))
    villain_observed = max((float(amount) for seat, amount in observed.items()
                            if seat != state.hero_seat), default=0.0)
    if hero_observed != hero_bet or villain_observed != villain_bet:
        return _unsupported("history_bet_totals_not_confirmed")
    if pot < hero_bet + villain_bet:
        return _unsupported("pot_does_not_cover_current_street_commitments")
    # Use the smaller share if a ruleset can produce an odd chip. This proof
    # must not depend on which seat receives that extra chip.
    tie_payoff = floor((pot + call) / 2) - call
    if tie_payoff <= 0:
        return None, {"status": "unsupported", "reason": "royal_tie_call_not_strictly_better",
                      "tie_payoff": tie_payoff}
    return Policy({"CALL": 1}, "public_royal_flush_tie_ev_positive"), {
        "status": "decided", "reason": "public_royal_flush_tie_ev_positive",
        "guaranteed_showdown": "shared_public_royal_flush", "tie_payoff": tie_payoff,
        "pot_contract": "state.pot includes the current opponent bet",
    }


def _unsupported(reason: str, **detail: Any) -> tuple[None, dict[str, Any]]:
    return None, {"status": "unsupported", "reason": reason, **detail}


def _number(value: Any, name: str) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if isfinite(result) else None


def terminal_decision(state: Any, equity: Any, enabled: bool = False) -> tuple[Policy | None, dict[str, Any]]:
    """Return an all-in showdown call/fold override, or ``None`` plus diagnostics.

    ``equity`` is structurally compatible with ``ranges.EquityResult``.  Its
    uncertainty interval is numerical Monte Carlo uncertainty only; an exact
    river enumeration is exact *against the supplied range*, not proof that the
    opponent range is correct.
    """
    if not enabled:
        return None, {"status": "disabled", "reason": "terminal_candidate_off"}

    required = ("num_players", "pot", "to_call", "hero_stack", "villain_stack",
                "hero_bet", "villain_bet", "street", "hero_seat", "action_history", "valid_actions")
    absent = [name for name in required if not hasattr(state, name)]
    if absent:
        return _unsupported("missing_state_fields", missing=absent)
    if state.num_players != 2:
        return _unsupported("not_heads_up", num_players=state.num_players)

    pot = _number(state.pot, "pot")
    call = _number(state.to_call, "to_call")
    hero_stack = _number(state.hero_stack, "hero_stack")
    villain_stack = _number(state.villain_stack, "villain_stack")
    hero_bet = _number(state.hero_bet, "hero_bet")
    villain_bet = _number(state.villain_bet, "villain_bet")
    if None in (pot, call, hero_stack, villain_stack, hero_bet, villain_bet):
        return _unsupported("non_finite_chip_value")
    if min(pot, call, hero_stack, villain_stack, hero_bet, villain_bet) < 0:
        return _unsupported("negative_chip_value")
    if call <= 0:
        return _unsupported("no_facing_bet")
    if call > hero_stack:
        return _unsupported("call_exceeds_available_stack")
    if not isinstance(state.valid_actions, (tuple, list, set)):
        return _unsupported("invalid_valid_actions")
    if "call" not in state.valid_actions or "fold" not in state.valid_actions:
        return _unsupported("call_or_fold_not_legal", valid_actions=tuple(state.valid_actions))

    # There is no future betting only when villain is all-in, or this call puts
    # hero all-in.  A non-terminal bet must remain with the regular policy:
    # showdown equity is not its final EV.
    if villain_stack > 0 and call < hero_stack:
        return _unsupported("future_betting_possible")
    if villain_bet - hero_bet != call:
        return _unsupported("uncalled_or_noncanonical_bet_gap",
                            observed_gap=villain_bet - hero_bet, to_call=call)

    # normalize() can repair an incomplete history using authoritative to_call.
    # Do not let that repair conceal an opponent's uncalled excess raise.
    history = tuple(state.action_history)
    if not history:
        return _unsupported("history_cannot_confirm_contestable_pot")
    try:
        observed = street_bets(history, state.street)
    except (KeyError, TypeError, ValueError):
        return _unsupported("history_cannot_confirm_contestable_pot")
    hero_observed = float(observed.get(state.hero_seat, 0))
    villain_observed = max((float(amount) for seat, amount in observed.items()
                            if seat != state.hero_seat), default=0.0)
    if hero_observed != hero_bet or villain_observed != villain_bet:
        return _unsupported("history_bet_totals_not_confirmed",
                            observed_hero_bet=hero_observed, observed_villain_bet=villain_observed,
                            normalized_hero_bet=hero_bet, normalized_villain_bet=villain_bet)
    if pot < hero_bet + villain_bet:
        return _unsupported("pot_does_not_cover_current_street_commitments",
                            pot=pot, current_street_commitments=hero_bet + villain_bet)

    estimate = _number(getattr(equity, "equity", None), "equity")
    stderr = _number(getattr(equity, "stderr", None), "stderr")
    if estimate is None or stderr is None or not 0 <= estimate <= 1 or stderr < 0:
        return _unsupported("invalid_equity_estimate")

    # Equity is win + half-tie.  Therefore these payoff rows reduce exactly to
    # E * (P + C) - C: win=P, tie=(P-C)/2, lose=-C.
    lower_equity, upper_equity = max(0.0, estimate - 2 * stderr), min(1.0, estimate + 2 * stderr)
    def ev(value: float) -> float:
        return value * (pot + call) - call
    lower_ev, point_ev, upper_ev = ev(lower_equity), ev(estimate), ev(upper_equity)
    diagnostics = {
        "status": "decided",
        "terminal": True,
        "pot_contract": "state.pot includes the current opponent bet; sunk hero chips are excluded",
        "payoffs": {"win": pot, "tie": (pot - call) / 2, "lose": -call},
        "equity": estimate,
        "numerical_interval": [lower_equity, upper_equity],
        "ev_interval": [lower_ev, upper_ev],
        "point_ev": point_ev,
        "equity_method": getattr(equity, "method", "unknown"),
        "samples": getattr(equity, "samples", None),
        "exact_against_supplied_range": bool(getattr(equity, "exact", False)),
        "range_uncertainty": "unquantified: exact enumeration does not validate the supplied range",
    }
    if lower_ev > 0:
        return Policy({"CALL": 1}, "terminal_ev_lower_bound_positive"), diagnostics
    if upper_ev < 0:
        return Policy({"FOLD": 1}, "terminal_ev_upper_bound_negative"), diagnostics
    diagnostics["reason"] = "interval_crosses_or_touches_zero_keep_original_policy"
    return None, diagnostics
