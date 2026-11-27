"""Bounded vertical-slice evidence and gating for the v1.10 candidate.

This module is deliberately conservative.  It describes a decision node and
the evidence available at that node, but it never treats a synthetic offline
EV table as live truth.  A strategy can only pass the gate when the state,
cashflow, legal action set, conditional response model, downside, and runtime
budget are all explicit and complete.
"""

from dataclasses import asdict, dataclass
import hashlib
import json
from typing import Any, Mapping


@dataclass(frozen=True, slots=True)
class NodeKey:
    street: str
    node_id: str
    position: str
    effective_stack_bb: float
    big_blind: int
    ante_by_seat: tuple[int, ...]
    hand_start_stacks: tuple[int | None, ...]
    pot: int
    to_call: int
    hero_bet: int
    villain_bet: int
    min_raise_to: int
    max_raise_to: int
    action_line: tuple[str, ...]
    legal_actions: tuple[str, ...]
    data_sources: tuple[str, ...]

    @property
    def digest(self) -> str:
        payload = json.dumps(asdict(self), sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(payload.encode()).hexdigest()[:16]


@dataclass(frozen=True, slots=True)
class RangeBelief:
    label: str
    source: str
    opportunities: int
    effective_samples: int
    complete: bool
    uncertainty: str
    limitations: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ActionValueEvidence:
    equity_probability: float | None
    equity_uncertainty_probability: float | None
    action_ev_bb: tuple[tuple[str, float], ...] | None
    action_ev_status: str
    action_ev_reason: str
    complete: bool
    known_downside_bb: float | None
    model_errors: tuple[str, ...]
    same_world: bool


def _ante_by_seat(state: Any) -> tuple[int, ...]:
    values = [0, 0]
    for event in state.action_history:
        if event.get("action") == "post_ante":
            seat = int(event.get("seat", 0))
            if 0 <= seat < len(values):
                values[seat] += max(0, int(event.get("amount", 0)))
    return tuple(values)


def _legal_actions(state: Any) -> tuple[str, ...]:
    result = []
    for action in sorted(str(value) for value in state.valid_actions):
        if action in {"fold", "check"}:
            result.append(f"{action}:0")
        elif action == "call":
            result.append(f"call:{int(state.to_call)}")
        elif action == "raise":
            result.append(f"raise_to:{int(state.min_raise)}:{int(state.max_raise)}")
        elif action == "all_in":
            result.append(f"all_in_to:{int(state.hero_bet + state.hero_stack)}")
        else:
            result.append(f"{action}:unknown")
    return tuple(result)


def build_node_key(state: Any, snapshot: Any = None) -> NodeKey:
    """Build an immutable key with explicit rules, stacks, prices, and sources."""
    line = tuple(
        f"{event.get('phase', 'preflop')}:{event.get('seat')}:{event.get('action')}:{event.get('amount', 0)}"
        for event in state.action_history
    )
    starts = tuple(snapshot.hand_start_stacks) if snapshot is not None else (None, None)
    sources = {"sdk_authority", "action_history"}
    if snapshot is not None:
        sources.add(str(snapshot.rules.source))
        sources.add(str(snapshot.ledger.source))
    return NodeKey(
        state.street,
        state.node.id,
        state.position,
        round(float(state.effective_stack_bb), 3),
        int(state.big_blind),
        _ante_by_seat(state),
        starts,
        int(state.pot),
        int(state.to_call),
        int(state.hero_bet),
        int(state.villain_bet),
        int(state.min_raise),
        int(state.max_raise),
        line,
        _legal_actions(state),
        tuple(sorted(sources)),
    )


def _range_belief(label: str, value: Any) -> RangeBelief:
    summary = value.summary() if value is not None else {}
    return RangeBelief(
        label=label,
        source=str(summary.get("model", "unavailable")),
        opportunities=int(summary.get("opportunities", 0) or 0),
        effective_samples=int(summary.get("samples", 0) or 0),
        complete=bool(getattr(value, "complete", False) and getattr(value, "weights", {})),
        uncertainty="range_model" if not (getattr(value, "complete", False)
                                           and getattr(value, "weights", {})) else "bounded",
        limitations=tuple(summary.get("limitations", ()) or ()),
    )


def assess_slice(state: Any, snapshot: Any, villain_range: Any, hero_range: Any,
                 equity: Any, *, elapsed_ms: float | None = None,
                 budget_ms: float = 450.0) -> dict[str, Any]:
    """Return bounded evidence and a non-mutating strategy-gate decision.

    The live candidate currently has no new default-on action policy.  This
    gate is nevertheless used in production traces so later evidence cannot
    bypass price, cashflow, legality, or model-completeness checks.
    """
    key = build_node_key(state, snapshot)
    accounting = snapshot.ledger
    ranges = {
        "hero": _range_belief("hero", hero_range),
        "villain": _range_belief("villain", villain_range),
    }
    limitations = []
    if not snapshot.ledger.strategy_safe:
        limitations.extend(snapshot.conflicts or ("accounting_inconsistent",))
    if not ranges["hero"].complete or not ranges["villain"].complete:
        limitations.append("conditional_range_incomplete")
    if state.to_call and accounting.authoritative_to_call != state.to_call:
        limitations.append("authority_to_call_not_reconciled")
    if accounting.potential_uncalled:
        limitations.append("potential_uncalled_return_unknown")
    if state.min_raise > state.max_raise or state.max_raise < 0:
        limitations.append("invalid_raise_bounds")
    if elapsed_ms is not None and elapsed_ms > budget_ms:
        limitations.append("decision_budget_exceeded")

    # This is intentionally a diagnostic envelope, not an EV claim.  The
    # existing equity estimate is a checkdown estimate and does not contain
    # opponent fold/call/raise response or future-street values.
    eq_uncertainty = float(getattr(equity, "stderr", 0.0) or 0.0)
    evidence = ActionValueEvidence(
        equity_probability=round(float(getattr(equity, "equity", 0.5)), 6),
        equity_uncertainty_probability=round(eq_uncertainty, 6),
        action_ev_bb=None,
        action_ev_status="model_limited",
        action_ev_reason="conditioned_response_and_future_street_model_missing",
        complete=False,
        known_downside_bb=None,
        model_errors=(
            "no_conditioned_fold_call_raise_response",
            "no_complete_future_street_tree",
            "checkdown_equity_is_not_action_ev",
        ),
        same_world=False,
    )
    river_large_active = False
    prior_reason = "not_turn_or_river"
    first_active = None
    if state.street in {"turn", "river"}:
        street_events = [event for event in state.action_history
                         if event.get("phase", "preflop") == state.street]
        first_aggressive = next((event for event in street_events
                                 if event.get("action") in {"raise", "all_in"}), None)
        if first_aggressive is None:
            prior_reason = "no_current_street_active_bet"
        else:
            first_active = {
                "seat": int(first_aggressive.get("seat", -1)),
                "action": str(first_aggressive.get("action")),
                "amount": int(first_aggressive.get("amount", 0)),
            }
            fraction = int(first_aggressive.get("amount", 0)) / max(1, int(state.pot))
            earlier_aggressive = any(event.get("action") in {"raise", "all_in"}
                                     for event in street_events
                                     if event is not first_aggressive)
            earlier_check_by_bettor = any(
                event.get("seat") == first_aggressive.get("seat")
                and event.get("action") == "check"
                for event in street_events[:street_events.index(first_aggressive)])
            if state.street not in {"turn", "river"}:
                prior_reason = "street_not_in_prior_scope"
            elif int(first_aggressive.get("seat", -1)) != 1 - state.hero_seat:
                prior_reason = "first_active_was_hero"
            elif earlier_aggressive:
                prior_reason = "not_first_active_bet"
            elif earlier_check_by_bettor:
                prior_reason = "opponent_check_raise"
            elif fraction < 0.75:
                prior_reason = "opponent_bet_below_large_threshold"
            else:
                river_large_active = True
                prior_reason = "eligible_current_street_opponent_first_large_bet"
    return {
        "node_key": asdict(key),
        "node_digest": key.digest,
        "ranges": {name: asdict(value) for name, value in ranges.items()},
        "action_value": asdict(evidence),
        "rule_and_cashflow": {
            "strategy_safe": bool(accounting.strategy_safe),
            "status": snapshot.status,
            "conflicts": list(snapshot.conflicts),
            "contestable_pot": accounting.contestable_pot,
            "potential_uncalled": accounting.potential_uncalled,
            "authoritative_to_call": accounting.authoritative_to_call,
        },
        "low_bluff_prior_scope": {
            "eligible": river_large_active,
            "scope": "opponent_turn_or_river_large_active_bet_only",
            "applied_to_strategy": False,
            "first_active_event": first_active,
            "reason": prior_reason,
        },
        "resource": {
            "elapsed_ms": round(elapsed_ms, 3) if elapsed_ms is not None else None,
            "budget_ms": budget_ms,
            "within_budget": elapsed_ms is None or elapsed_ms <= budget_ms,
        },
        "strategy_gate": {
            "module": "v1.10_vertical_slice",
            "enabled": False,
            "passed": False,
            "reason": "action_value_model_incomplete",
            "limitations": list(dict.fromkeys(limitations + list(evidence.model_errors))),
        },
    }


def gate_summary(trace: Mapping[str, Any]) -> dict[str, Any]:
    """Keep a small bounded summary suitable for match-local counters."""
    gate = trace.get("strategy_gate", {})
    return {
        "enabled": bool(gate.get("enabled", False)),
        "passed": bool(gate.get("passed", False)),
        "reason": str(gate.get("reason", "unknown")),
        "node_digest": str(trace.get("node_digest", "")),
    }
