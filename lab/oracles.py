"""Small, dependency-free terminal oracle for F03.

This module deliberately does not import the production engine.  Amounts are
chips and ``pot`` is the pot already present at the decision point; sunk money
is therefore never charged again.
"""
from __future__ import annotations

from dataclasses import dataclass
from math import isfinite, log
from typing import Iterable, Mapping

OUTCOMES = ("win", "tie", "lose")
RESPONSES = ("fold", "call", "raise_fold")
PREDICTION_CLASSES = ("fold", "call", "raise")


def _finite_number(value: float, name: str) -> float:
    value = float(value)
    if not isfinite(value):
        raise ValueError(f"{name} must be finite")
    return value


def _probabilities(values: Mapping[str, float], names: Iterable[str]) -> dict[str, float]:
    names = tuple(names)
    if not names or len(set(names)) != len(names) or set(values) - set(names):
        raise ValueError("probability classes must be distinct and contain every supplied key")
    out = {name: _finite_number(values.get(name, 0.0), f"probability {name}")
           for name in names}
    if any(v < 0 for v in out.values()):
        raise ValueError("probabilities must be non-negative")
    total = sum(out.values())
    if abs(total - 1.0) > 1e-9:
        raise ValueError(f"probabilities must sum to one (got {total})")
    return {k: max(0.0, v) for k, v in out.items()}


@dataclass(frozen=True)
class Branch:
    """A conditional response branch of a hero bet."""

    probability: float
    response: str
    outcomes: Mapping[str, float] | None = None


@dataclass(frozen=True)
class Terminal:
    response: str
    outcome: str | None
    probability: float
    payoff: float


def showdown_payoff(pot: float, outcome: str) -> float:
    """Net hero profit for a check/showdown with no new hero investment."""
    pot = _finite_number(pot, "pot")
    if pot < 0:
        raise ValueError("pot must be non-negative")
    if outcome == "win":
        return float(pot)
    if outcome == "tie":
        return float(pot) / 2.0
    if outcome == "lose":
        return 0.0
    raise ValueError(f"unknown outcome: {outcome}")


def call_payoff(pot: float, call_amount: float, outcome: str) -> float:
    """Net profit after calling ``call_amount``.

    ``pot`` already includes the opponent's preceding bet.  On a tie the
    returned half-pot includes the hero's call, which is then subtracted.
    """
    pot = _finite_number(pot, "pot")
    call_amount = _finite_number(call_amount, "call_amount")
    if pot < 0 or call_amount < 0:
        raise ValueError("pot and call_amount must be non-negative")
    if outcome == "win":
        return float(pot)
    if outcome == "tie":
        return (float(pot) + call_amount) / 2.0 - call_amount
    if outcome == "lose":
        return -float(call_amount)
    raise ValueError(f"unknown outcome: {outcome}")


def enumerate_bet(pot: float, bet: float, branches: Iterable[Branch]) -> list[Terminal]:
    """Expand conditional F/C/R branches into terminal probability/payoff rows.

    For a fold, the opponent's uncalled excess raise is returned to them and
    hero loses exactly their new bet.  Thus a raise-fold branch is ``-bet``;
    it never incorrectly charges the raise amount.
    """
    pot = _finite_number(pot, "pot")
    bet = _finite_number(bet, "bet")
    if pot < 0 or bet < 0:
        raise ValueError("pot and bet must be non-negative")
    rows: list[Terminal] = []
    total = 0.0
    for branch in branches:
        p = _finite_number(branch.probability, "branch probability")
        if p < 0:
            raise ValueError("branch probabilities must be non-negative")
        if branch.response not in RESPONSES:
            raise ValueError(f"unknown response: {branch.response}")
        total += p
        if branch.response in ("fold", "raise_fold"):
            rows.append(Terminal(branch.response, None, p, pot if branch.response == "fold" else -bet))
            continue
        outcomes = _probabilities(branch.outcomes or {}, OUTCOMES)
        for outcome, q in outcomes.items():
            rows.append(Terminal("call", outcome, p * q, call_payoff(pot + bet, bet, outcome)))
    if abs(total - 1.0) > 1e-9:
        raise ValueError(f"branch probabilities must sum to one (got {total})")
    return rows


def expected_value(rows: Iterable[Terminal]) -> float:
    rows = list(rows)
    if not rows:
        raise ValueError("at least one terminal row is required")
    for row in rows:
        probability = _finite_number(row.probability, "terminal probability")
        _finite_number(row.payoff, "terminal payoff")
        if probability < 0:
            raise ValueError("terminal probabilities must be non-negative")
    if abs(sum(row.probability for row in rows) - 1.0) > 1e-9:
        raise ValueError("terminal probabilities must sum to one")
    result = sum(row.probability * row.payoff for row in rows)
    return _finite_number(result, "expected value")


def check_ev(pot: float, win: float, tie: float, lose: float) -> float:
    return expected_value([Terminal("check", outcome, p, showdown_payoff(pot, outcome))
                           for outcome, p in _probabilities({"win": win, "tie": tie, "lose": lose}, OUTCOMES).items()])


def call_ev(pot: float, call_amount: float, win: float, tie: float, lose: float) -> float:
    outcomes = _probabilities({"win": win, "tie": tie, "lose": lose}, OUTCOMES)
    return sum(p * call_payoff(pot, call_amount, outcome) for outcome, p in outcomes.items())


def bet_fold_ev(pot: float, bet: float, fold: float, call: float, raise_fold: float,
                call_win: float, call_tie: float, call_lose: float) -> float:
    rows = enumerate_bet(pot, bet, (
        Branch(fold, "fold"),
        Branch(call, "call", {"win": call_win, "tie": call_tie, "lose": call_lose}),
        Branch(raise_fold, "raise_fold"),
    ))
    return expected_value(rows)


def brier_score(predicted: Mapping[str, float], actual: str,
                classes: Iterable[str] = PREDICTION_CLASSES) -> float:
    """Brier score; defaults to predicted fold/call/raise classes."""
    classes = tuple(classes)
    if actual not in classes:
        raise ValueError(f"unknown class: {actual}")
    p = _probabilities(predicted, classes)
    return sum((p[name] - (1.0 if name == actual else 0.0)) ** 2 for name in classes)


def log_loss(predicted: Mapping[str, float], actual: str, epsilon: float = 1e-12,
             classes: Iterable[str] = PREDICTION_CLASSES) -> float:
    epsilon = _finite_number(epsilon, "epsilon")
    if epsilon <= 0 or epsilon >= 1:
        raise ValueError("epsilon must be between zero and one")
    classes = tuple(classes)
    if actual not in classes:
        raise ValueError(f"unknown class: {actual}")
    p = _probabilities(predicted, classes)[actual]
    return -log(max(p, epsilon))


def weighted_ev(action_evs: Mapping[str, float], strategy: Mapping[str, float]) -> float:
    """EV of a legal mixed strategy, with explicit probability normalization."""
    probabilities = _probabilities(strategy, action_evs)
    return sum(probabilities[a] * _finite_number(value, "action EV")
               for a, value in action_evs.items())


def regret(action_evs: Mapping[str, float], strategy: Mapping[str, float]) -> float:
    """Oracle best legal action EV minus the tested mixed strategy EV."""
    if not action_evs:
        raise ValueError("at least one legal action is required")
    return max(action_evs.values()) - weighted_ev(action_evs, strategy)


def perturb_response(probabilities: Mapping[str, float], target: str, delta: float,
                     source: str | None = None) -> dict[str, float]:
    """Transfer ``delta`` mass to target (negative removes it), then verify simplex."""
    p = _probabilities(probabilities, RESPONSES)
    delta = _finite_number(delta, "delta")
    if target not in RESPONSES or (source is not None and source not in RESPONSES):
        raise ValueError("target/source must be fold, call, or raise_fold")
    if source is None:
        candidates = [a for a in RESPONSES if a != target]
        source = max(candidates, key=p.get)
    if source == target:
        raise ValueError("source and target must differ")
    if delta >= 0:
        if p[source] + 1e-12 < delta:
            raise ValueError("not enough source probability")
        p[target] += delta
        p[source] -= delta
    else:
        amount = -delta
        if p[target] + 1e-12 < amount:
            raise ValueError("not enough target probability")
        p[target] -= amount
        p[source] += amount
    return _probabilities(p, RESPONSES)


@dataclass(frozen=True)
class HiddenWorld:
    name: str
    visible_state: str
    response_marginals: Mapping[str, float]
    call_composition: Mapping[str, float]


def indistinguishable_fcr_fixture() -> tuple[HiddenWorld, HiddenWorld]:
    """Two worlds with identical visible F/C/R frequencies but different calls."""
    marginal = {"fold": 0.30, "call": 0.60, "raise_fold": 0.10}
    first = HiddenWorld("opponent_weaker_calling_range", "same-visible-river", marginal,
                        {"win": 0.75, "tie": 0.05, "lose": 0.20})
    second = HiddenWorld("opponent_stronger_calling_range", "same-visible-river", marginal,
                        {"win": 0.20, "tie": 0.05, "lose": 0.75})
    return first, second


def hand_checked_examples() -> dict[str, float]:
    """Reference values from F03 spec, useful for reports and smoke tests."""
    return {
        "check_100": check_ev(100, .45, .20, .35),
        "call_25": call_ev(100, 25, .40, .20, .40),
        "bet_fold": bet_fold_ev(100, 50, .30, .60, .10, .60, 0.0, .40),
        "all_fold": bet_fold_ev(100, 50, 1.0, 0.0, 0.0, 1.0, 0.0, 0.0),
        "all_call_tie": bet_fold_ev(100, 50, 0.0, 1.0, 0.0, 0.0, 1.0, 0.0),
        "all_raise_fold": bet_fold_ev(100, 50, 0.0, 0.0, 1.0, 1.0, 0.0, 0.0),
    }
