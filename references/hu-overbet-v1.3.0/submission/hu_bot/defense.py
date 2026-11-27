"""Opt-in river overbet call/fold defense based on declared range components.

The component bluff share and value-shape stress bands are heuristic
sensitivity inputs, not calibrated opponent frequencies.
"""
from __future__ import annotations

from math import isfinite
from time import perf_counter
from typing import Any, Mapping

from .core import Policy, street_bets
from .poker import evaluate


EPSILON = 1e-9


def _none(reason: str, **detail: Any) -> tuple[None, dict[str, Any]]:
    return None, {"complete": False, "reason": reason, **detail}


def _finite(value: Any) -> float | None:
    try:
        value = float(value)
    except (TypeError, ValueError):
        return None
    return value if isfinite(value) else None


def _expired(deadline: Any) -> bool | None:
    value = _finite(deadline)
    return None if value is None else perf_counter() >= value


def _combo_ok(combo: Any, blocked: set[str]) -> bool:
    return (isinstance(combo, tuple) and len(combo) == 2 and
            all(isinstance(card, str) and len(card) == 2 for card in combo) and
            len(set(combo)) == 2 and not blocked.intersection(combo))


def _distribution(weights: Any, *, blocked: set[str], full_weights: Mapping | None,
                  deadline: float, label: str) -> tuple[dict | None, str | None]:
    """Validate a normalized conditional component without publishing prefixes."""
    if not isinstance(weights, Mapping) or not weights:
        return None, f"{label}_missing_or_empty"
    clean: dict = {}
    total = 0.0
    for index, (combo, mass) in enumerate(weights.items()):
        if index % 32 == 0 and perf_counter() >= deadline:
            return None, "deadline_atomic_discard"
        value = _finite(mass)
        if value is None or value <= 0 or not _combo_ok(combo, blocked):
            return None, f"{label}_invalid_combo_or_weight"
        if full_weights is not None and (combo not in full_weights or _finite(full_weights[combo]) is None
                                         or float(full_weights[combo]) <= 0):
            return None, f"{label}_outside_complete_range"
        clean[combo] = value
        total += value
    if abs(total - 1.0) > EPSILON:
        return None, f"{label}_not_normalized"
    return clean, None


def _call_fold_baseline(base_policy: Policy) -> tuple[float | None, dict[str, Any] | None, str | None]:
    try:
        weights = base_policy.normalized().weights
    except (AttributeError, ValueError, TypeError):
        return None, None, "invalid_base_policy"
    fold, call, converted = 0.0, 0.0, 0.0
    for label, mass in weights.items():
        if label == "FOLD":
            fold += mass
        elif label == "CALL":
            call += mass
        elif label == "ALL_IN" or label.startswith("RAISE"):
            call += mass
            converted += mass
        else:
            return None, None, "base_policy_has_unsupported_facing_action"
    total = fold + call
    if total <= 0:
        return None, None, "empty_call_fold_baseline"
    return call / total, {"fold_mass": fold / total, "call_mass": call / total,
                            "raise_or_allin_mass_converted_to_call": converted / total}, None


def defend_river_overbet(state: Any, base_policy: Policy, villain_range: Any,
                         equity_result: Any, deadline: float, enabled: bool = False,
                         mix_weight: float = .35) -> tuple[Policy | None, dict[str, Any]]:
    """Conservatively blend a declared river overbet call/fold target with base.

    The rule is a sensitivity/regret rule over a declared bluff-share interval,
    not a GTO solution and not a calibrated safety guarantee.
    """
    if not enabled:
        return None, {"complete": False, "reason": "river_overbet_defense_disabled"}
    expired = _expired(deadline)
    if expired is None:
        return _none("invalid_deadline")
    if expired:
        return _none("deadline_atomic_discard")
    if bool(getattr(equity_result, "nuts", False)):
        return None, {"complete": False, "reason": "nuts_preserve_base_policy",
                      "note": "No component defense override is applied to an existing nuts policy."}
    if not bool(getattr(equity_result, "exact", False)):
        return _none("exact_river_equity_required_to_check_nuts")
    mix = _finite(mix_weight)
    if mix is None or not 0 <= mix <= 1:
        return _none("invalid_mix_weight")

    needed = ("street", "board", "hero_cards", "hero_seat", "num_players", "pot", "to_call",
              "hero_stack", "hero_bet", "villain_bet", "valid_actions", "action_history")
    missing = [name for name in needed if not hasattr(state, name)]
    if missing:
        return _none("missing_state_fields", missing=missing)
    if state.street != "river" or len(state.board) != 5 or len(state.hero_cards) != 2:
        return _none("not_river_showdown_state")
    if state.num_players != 2:
        return _none("not_heads_up", num_players=state.num_players)
    if not isinstance(state.valid_actions, (tuple, list, set)):
        return _none("invalid_valid_actions")
    if "fold" not in state.valid_actions or "call" not in state.valid_actions:
        return _none("call_or_fold_not_legal", valid_actions=tuple(state.valid_actions))

    values = {name: _finite(getattr(state, name))
              for name in ("pot", "to_call", "hero_stack", "hero_bet", "villain_bet")}
    if any(value is None for value in values.values()) or min(values.values()) < 0:
        return _none("invalid_chip_value")
    pot, call, hero_stack, hero_bet, villain_bet = (values[name] for name in
                                                     ("pot", "to_call", "hero_stack", "hero_bet", "villain_bet"))
    if call <= 0 or hero_bet != 0:
        return _none("not_unopened_river_facing_bet")
    if villain_bet - hero_bet != call:
        return _none("uncalled_or_noncanonical_bet_gap",
                     observed_gap=villain_bet-hero_bet, to_call=call)
    if call > hero_stack:
        return _none("short_call_truncation_unsupported", to_call=call, hero_stack=hero_stack)
    pot_before = pot - call
    if pot_before <= 0:
        return _none("nonpositive_pot_before_bet")
    ratio = call / pot_before
    if ratio + EPSILON < 1.25:
        return _none("below_overbet_threshold", ratio=ratio)

    history = tuple(state.action_history)
    if not history:
        return _none("history_cannot_confirm_first_bet")
    try:
        observed = street_bets(history, state.street)
    except (AttributeError, KeyError, TypeError, ValueError):
        return _none("history_cannot_confirm_first_bet")
    observed_hero = float(observed.get(state.hero_seat, 0))
    observed_villain = max((float(amount) for seat, amount in observed.items()
                            if seat != state.hero_seat), default=0.0)
    river_events = [event for event in history if event.get("phase", "preflop") == "river"]
    aggressive = [event for event in river_events if event.get("action") in {"raise", "all_in"}]
    try:
        aggressor_is_hero = len(aggressive) == 1 and int(aggressive[0].get("seat", -1)) == state.hero_seat
    except (AttributeError, TypeError, ValueError):
        return _none("history_not_first_villain_bet")
    if (observed_hero != hero_bet or observed_villain != villain_bet or len(aggressive) != 1 or
            aggressor_is_hero):
        return _none("history_not_first_villain_bet")

    if not bool(getattr(villain_range, "complete", False)) or not isinstance(getattr(villain_range, "weights", None), Mapping):
        return _none("incomplete_or_missing_villain_range")
    full_weights = villain_range.weights
    full, full_error = _distribution(full_weights, blocked=set(state.hero_cards + state.board),
                                     full_weights=None, deadline=float(deadline), label="villain_range")
    if full_error:
        return _none(full_error)
    components = getattr(villain_range, "river_components", None)
    if not isinstance(components, Mapping):
        return _none("river_components_unavailable")
    if components.get("calibrated") is not False:
        return _none("river_components_not_declared_uncalibrated")
    model = components.get("model")
    if not isinstance(model, str) or not model:
        return _none("river_components_invalid_model")
    component_ratio = _finite(components.get("ratio"))
    if component_ratio is None or abs(component_ratio-ratio) > EPSILON:
        return _none("river_components_ratio_mismatch", actual_ratio=ratio, component_ratio=component_ratio)
    bluff_share = _finite(components.get("bluff_share"))
    interval = components.get("bluff_interval")
    if (bluff_share is None or not 0 <= bluff_share <= 1 or not isinstance(interval, (tuple, list)) or
            len(interval) != 2):
        return _none("river_components_invalid_bluff_share")
    lo, hi = _finite(interval[0]), _finite(interval[1])
    if lo is None or hi is None or not 0 <= lo <= hi <= 1 or not lo-EPSILON <= bluff_share <= hi+EPSILON:
        return _none("river_components_invalid_bluff_interval")
    blocked = set(state.hero_cards + state.board)
    value_weights, error = _distribution(components.get("value_weights"), blocked=blocked,
                                         full_weights=full, deadline=float(deadline), label="value_weights")
    if error:
        return _none(error)
    bluff_weights, error = _distribution(components.get("bluff_weights"), blocked=blocked,
                                         full_weights=full, deadline=float(deadline), label="bluff_weights")
    if error:
        return _none(error)

    # Legacy explicit component oracles may omit stress shapes.  When supplied,
    # both shape distributions and their metadata must be complete: silently
    # dropping a malformed adverse shape would understate sensitivity.
    value_shapes = {"central": value_weights}
    stress = components.get("value_stress_weights")
    concentration = components.get("value_concentration")
    if stress is not None:
        shape_names = {"central", "wide", "tight"}
        if not isinstance(stress, Mapping) or set(stress) != {"wide", "tight"}:
            return _none("value_stress_weights_invalid_shapes")
        if not isinstance(concentration, Mapping) or set(concentration) != shape_names:
            return _none("value_concentration_invalid_shapes")
        for name in ("wide", "tight"):
            weights, error = _distribution(stress[name], blocked=blocked, full_weights=full,
                                           deadline=float(deadline), label=f"value_stress_{name}")
            if error:
                return _none(error)
            value_shapes[name] = weights
        for name in shape_names:
            metadata = concentration[name]
            if not isinstance(metadata, Mapping):
                return _none("value_concentration_invalid_metadata", shape=name)
            cut, slope = _finite(metadata.get("percentile_cut")), _finite(metadata.get("slope"))
            if cut is None or slope is None:
                return _none("value_concentration_invalid_metadata", shape=name)

    base_call, base_detail, error = _call_fold_baseline(base_policy)
    if error:
        return _none(error)
    try:
        hero_rank = evaluate(tuple(state.hero_cards) + tuple(state.board))
    except (IndexError, ValueError):
        return _none("invalid_hero_or_board_cards")
    rank_cache: dict[tuple[str, str], tuple] = {}

    def component_equity(weights: Mapping, label: str) -> tuple[float | None, str | None]:
        total = 0.0
        for index, (combo, mass) in enumerate(weights.items()):
            if index % 32 == 0 and perf_counter() >= deadline:
                return None, "deadline_atomic_discard"
            try:
                if combo not in rank_cache:
                    rank_cache[combo] = evaluate(combo + tuple(state.board))
                villain_rank = rank_cache[combo]
            except (IndexError, ValueError):
                return None, f"{label}_invalid_card"
            total += mass * (1.0 if hero_rank > villain_rank else .5 if hero_rank == villain_rank else 0.0)
        return total, None

    value_equities = {}
    for name, weights in value_shapes.items():
        value_equity, error = component_equity(weights, f"value_{name}")
        if error:
            return _none(error)
        value_equities[name] = value_equity
    bluff_equity, error = component_equity(bluff_weights, "bluff_weights")
    if error:
        return _none(error)
    if perf_counter() >= deadline:
        return _none("deadline_atomic_discard")

    def equity_at(value_equity: float, q: float) -> float:
        return (1-q)*value_equity + q*bluff_equity

    def call_ev(equity: float) -> float:
        return equity*(pot+call)-call

    point_equity = equity_at(value_equities["central"], bluff_share)
    shape_endpoint_evs = {name: {"lo": call_ev(equity_at(value_equity, lo)),
                                 "hi": call_ev(equity_at(value_equity, hi))}
                          for name, value_equity in value_equities.items()}
    endpoint_evs = (shape_endpoint_evs["central"]["lo"], shape_endpoint_evs["central"]["hi"])
    all_endpoints = [ev for endpoints in shape_endpoint_evs.values() for ev in endpoints.values()]
    a, b = min(all_endpoints), max(all_endpoints)
    if abs(a) <= EPSILON and abs(b) <= EPSILON:
        return None, {"complete": False, "reason": "all_component_endpoint_evs_zero_keep_base",
                      "endpoint_evs": endpoint_evs, "value_shape_equity": value_equities,
                      "value_shape_endpoint_evs": shape_endpoint_evs,
                      "value_concentration": concentration}
    if a >= 0:
        target_call, target_rule = 1.0, "lower_endpoint_nonnegative_call"
    elif b <= 0:
        target_call, target_rule = 0.0, "upper_endpoint_nonpositive_fold"
    else:
        target_call, target_rule = b/(b-a), "crossing_minimax_regret"
    final_call = (1-mix)*base_call + mix*target_call
    final = Policy({"CALL": final_call, "FOLD": 1-final_call}, "river_overbet_component_sensitivity")
    return final.normalized(), {
        "complete": True,
        "model": model,
        "calibrated": False,
        "pot_before_bet": pot_before,
        "to_call": call,
        "overbet_ratio": ratio,
        "pot_odds": call/(pot+call),
        "component_equity": {"value": value_equities["central"], "bluff": bluff_equity, "point": point_equity},
        "value_shape_equity": value_equities,
        "value_shape_endpoint_evs": shape_endpoint_evs,
        "value_concentration": concentration,
        "bluff_share": bluff_share,
        "bluff_interval": [lo, hi],
        "point_ev": call_ev(point_equity),
        "endpoint_evs": {"lo": endpoint_evs[0], "hi": endpoint_evs[1]},
        "ev_interval": [a, b],
        "target_call_probability": target_call,
        "target_rule": target_rule,
        "base_call_probability": base_call,
        "base_call_fold": base_detail,
        "final_call_probability": final_call,
        "mix_weight": mix,
        "raise_removal": "Converts base CALL/RAISE*/ALL_IN mass to call for this call/fold defense; this is a conservative constraint awaiting validation, not proof that the original raise EV is negative.",
        "uncertainty": "Bluff-share interval and value-shape stress bands are heuristic sensitivity inputs; neither bluff frequency nor range-shape error is calibrated.",
        "equity_result_nuts": False,
    }
