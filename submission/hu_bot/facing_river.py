"""Opt-in guard for unpriced non-nut river value raises.

This does not assert that a raise is bad. It leaves a modeled call in place
when the policy would otherwise raise by an absolute-strength threshold while
the opponent's response to the raise is unknown.
"""
from __future__ import annotations

from math import isfinite
from time import perf_counter

from .core import Policy, street_bets


def guard_unpriced_raise(state, base_policy, villain_range, equity_result,
                        deadline, enabled=False):
    if not enabled:
        return None, {"applied": False, "reason": "disabled"}
    if perf_counter() >= deadline:
        return None, {"applied": False, "reason": "deadline"}
    if (state.street != "river" or len(state.board) != 5 or state.num_players != 2
            or state.to_call <= 0 or not {"fold", "call"} <= set(state.valid_actions)
            or "raise" not in state.valid_actions):
        return None, {"applied": False, "reason": "unsupported_node"}
    if base_policy.reason != "value_raise":
        return None, {"applied": False, "reason": "not_threshold_value_raise"}
    if (not villain_range.complete or not villain_range.weights
            or not equity_result.exact or equity_result.nuts):
        return None, {"applied": False, "reason": "range_or_equity_incomplete_or_nuts"}
    if (state.to_call > state.hero_stack or state.pot <= state.to_call
            or state.villain_bet-state.hero_bet != state.to_call):
        return None, {"applied": False, "reason": "uncanonical_price_or_short_call"}
    observed = street_bets(state.action_history, state.street)
    if (observed.get(state.hero_seat, 0) != state.hero_bet
            or max((v for seat, v in observed.items() if seat != state.hero_seat), default=0)
            != state.villain_bet):
        return None, {"applied": False, "reason": "unconfirmed_history"}
    try:
        weights = base_policy.normalized().weights
        equity = float(equity_result.equity)
    except (AttributeError, TypeError, ValueError):
        return None, {"applied": False, "reason": "invalid_policy_or_equity"}
    if not isfinite(equity) or not 0 <= equity <= 1:
        return None, {"applied": False, "reason": "invalid_equity"}
    raise_mass = sum(mass for label, mass in weights.items()
                     if label.startswith("RAISE") or label == "ALL_IN")
    if raise_mass <= 0 or any(label not in {"CALL", "FOLD", "ALL_IN"}
                             and not label.startswith("RAISE") for label in weights):
        return None, {"applied": False, "reason": "no_eligible_raise_mass"}
    call_ev = equity*(state.pot+state.to_call)-state.to_call
    if call_ev <= 0:
        return None, {"applied": False, "reason": "call_not_proven_positive"}
    if perf_counter() >= deadline:
        return None, {"applied": False, "reason": "deadline_atomic_discard"}
    guarded = Policy({"CALL": weights.get("CALL", 0)+raise_mass,
                      "FOLD": weights.get("FOLD", 0)},
                     "river_unpriced_raise_guard").normalized()
    return guarded, {"applied": True, "reason": "raise_response_unpriced",
                     "call_ev_vs_modeled_range": call_ev,
                     "raise_mass_to_call": raise_mass,
                     "model_calibrated": False,
                     "claim": "Call has positive EV against modeled betting range; no claim that raise EV is negative or that the range is calibrated."}
