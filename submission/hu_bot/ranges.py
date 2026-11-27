"""Blocker-aware combo posterior; likelihoods are documented heuristics."""
from bisect import bisect_left
from dataclasses import dataclass
from itertools import combinations
from math import exp, isfinite, sqrt
from time import perf_counter

from .core import STREETS, AGGRESSIVE
from .poker import DECK, evaluate, features, hand_class, preflop_score
from .history import ledger, preflop_context
from .preflop import lookup
from .opponent_actions import action_observation

COMBOS = tuple(combinations(DECK, 2))
PF_SCORES = {c: preflop_score(c) for c in COMBOS}
COMBO_CLASSES = {c: hand_class(c) for c in COMBOS}
CLASS_SCORES = {COMBO_CLASSES[c]: PF_SCORES[c] for c in COMBOS}


@dataclass
class RangeState:
    source_node: str
    weights: dict[tuple[str, str], float]
    confidence: float
    last_update: int
    complete: bool = True
    river_components: dict | None = None
    calibration: dict | None = None

    def summary(self):
        classes = {}
        for combo, weight in self.weights.items():
            key = hand_class(combo)
            classes[key] = classes.get(key, 0.0)+weight
        polar = self.river_components
        result = {"combos": len(self.weights), "confidence": round(self.confidence, 3),
                "last_update": self.last_update,
                "complete": self.complete, "source_node": self.source_node,
                "top_classes": sorted(classes.items(), key=lambda p: p[1], reverse=True)[:6],
                "river_components": None if polar is None else {
                    "bluff_share": polar["bluff_share"],
                    "bluff_interval": polar["bluff_interval"], "ratio": polar["ratio"],
                    "model": polar["model"], "calibrated": polar["calibrated"],
                    "value_concentration": polar["value_concentration"],
                    "value_combos": len(polar["value_weights"]),
                    "bluff_combos": len(polar["bluff_weights"])}}
        if self.calibration is not None:
            result["calibration"] = self.calibration
        return result


def sigmoid(x):
    return 1/(1+exp(max(-30, min(30, -x))))


def _odds_adjust(probability, multiplier):
    return probability*multiplier/(1-probability+probability*multiplier)


def _calibrated_likelihoods(weights, likelihoods, cell, choice, deadline):
    """Match a shrunk action frequency with a capped odds intercept.

    Aggregate actions set only the marginal; combo ordering remains heuristic.
    Folded hands have no inferred private cards.
    """
    total = sum(weights.values())
    if total <= 0 or not isfinite(total) or any(
            not isfinite(p) or not 0 < p < 1 for p in likelihoods.values()):
        return None
    baseline = sum(weights[c]*p for c, p in likelihoods.items())/total
    if not 0 < baseline < 1 or not isfinite(baseline):
        return None
    n = cell.opportunities
    target = (64*baseline+cell.counts.get(choice, 0))/(64+n)
    low, high = exp(-.7), exp(.7)
    for _ in range(18):
        if perf_counter() >= deadline:
            return None
        mid = (low+high)/2
        marginal = sum(weights[c]*_odds_adjust(p, mid) for c, p in likelihoods.items())/total
        if not isfinite(marginal):
            return None
        if marginal < target:
            low = mid
        else:
            high = mid
    multiplier = (low+high)/2
    return {c: _odds_adjust(p, multiplier) for c, p in likelihoods.items()}


def posterior(state, seat, deadline, max_updates=None, opponent_model=None,
              *, polar_river=False, action_likelihood_calibration=False):
    blocked = set(state.board)
    if seat != state.hero_seat:
        blocked.update(state.hero_cards)
    # Hero's public range must not collapse to its actual two private cards.
    combos = [c for c in COMBOS if not blocked.intersection(c)]
    weights = {c: 1.0 for c in combos}
    events = ledger(state.action_history, state.big_blind,
                    {state.hero_seat: state.hero_stack, 1-state.hero_seat: state.villain_stack})
    polar_event = None
    if (polar_river and state.street == "river" and seat != state.hero_seat
            and events and events[-1].index == len(state.action_history)-1):
        last = events[-1]
        if (last.seat == seat and last.street == "river" and not last.is_timeout
                and last.action in AGGRESSIVE and last.street_raises_before == 0
                and last.amount_to > last.table_bet and last.size_ratio >= 1.25):
            polar_event = last
    updates = 0
    river_components = None
    calibration = {"enabled": True, "applied": 0, "reasons": {}} if (
        action_likelihood_calibration and seat != state.hero_seat) else None

    def reason(name):
        if calibration is not None:
            counts = calibration["reasons"]
            counts[name] = min(255, counts.get(name, 0)+1)

    for event in events:
        if max_updates is not None and updates >= max_updates:
            break
        street, action = event.street, event.action
        if event.seat != seat or action.startswith("post_") or action == "fold" or event.is_timeout:
            continue
        board = state.board[:STREETS.get(street, 0)]
        if street != "preflop" and len(board) < 3:
            continue
        # Compute context and observed-size evidence once, not once per combo.
        ratio = event.size_ratio
        evidence_key = ("btn_open" if event.pf_raises_before == 0 and seat == state.dealer_seat
                        else "three_bet" if event.pf_raises_before == 1 else "four_bet")
        if street != "preflop":
            evidence_key = f"{street}_bet" if event.street_raises_before == 0 else f"{street}_raise"
        observed = None
        cell = None
        if calibration is not None and street != "preflop":
            observed = action_observation(event, state.hero_seat, state.dealer_seat)
            if observed is not None:
                cell = opponent_model.action_cells.get(observed[0]) if opponent_model is not None else None
                if cell is None:
                    reason("no_exact_cell")
                elif cell.opportunities < 24:
                    reason("sparse_cell")
                    cell = None
        # Exact-cell calibration supersedes the old broad adjustment for this event.
        adjustment = (opponent_model.range_adjustment(evidence_key)
                      if opponent_model is not None and cell is None else 0.0)
        # Precompute the 169 likelihoods for this exact historical decision node.
        class_likelihood = {}
        if street == "preflop":
            context, price = preflop_context(event, state.dealer_seat, state.big_blind)
            evidence_key = {"BTN_OPEN": "btn_open", "BB_LIMP": "isolate",
                            "BB_OPEN": "three_bet", "BTN_ISOLATION": "isolation_reraise"}.get(context, "four_bet")
            adjustment = opponent_model.range_adjustment(evidence_key) if opponent_model is not None else 0.0
            depth = max(1, event.effective_depth_bb)
            for klass, score in CLASS_SCORES.items():
                policy = lookup(context, depth, price, klass).weights
                if action in AGGRESSIVE:
                    labels = ("ALL_IN",) if event.is_jam else ("RAISE_SMALL", "RAISE_MEDIUM")
                    probability = sum(policy.get(label, 0) for label in labels)
                    # Broad unknown-opponent contamination prevents zeroing uncharted lines.
                    threshold = .36 if event.pf_raises_before == 0 else .62 if event.pf_raises_before == 1 else .79
                    observed_size = (event.amount_to/state.big_blind if event.pf_raises_before == 0
                                     else event.amount_to/max(state.big_blind, event.table_bet))
                    size_bonus = min(.22, .055*max(0, observed_size-(2.5 if event.pf_raises_before == 0 else 3.3)))
                    threshold += size_bonus
                    if event.is_jam:
                        threshold = max(threshold, .36 if depth <= 8 else .52 if depth <= 12
                                        else .65 if depth <= 25 else .80)
                    broad_prior = .02+.9*sigmoid((score-threshold+adjustment)*14)
                    probability = .75*probability+.25*broad_prior
                    if size_bonus:
                        probability *= .1+.9*sigmoid((score-.5-size_bonus)*12)
                    if adjustment:
                        probability = .85*probability+.15*sigmoid((score-threshold+adjustment)*14)
                else:
                    probability = policy.get("CALL" if action == "call" else "CHECK", 0)
                class_likelihood[klass] = .025+.95*probability
        if polar_event is not None and event.index == polar_event.index:
            if cell is not None:
                reason("polar_override")
            try:
                from . import polar
                mixed, components = polar.build(weights, board, ratio, deadline)
            except (TimeoutError, ValueError):
                total = sum(weights.values())
                return RangeState(state.node.id, {c: w/total for c, w in weights.items()},
                                  min(.75, updates*.1), updates, False, None, calibration)
            weights, river_components = mixed, components
            updates += 1
            continue
        new = {}
        likelihoods = {} if cell is not None else None
        for i, combo in enumerate(combos):
            if i % 32 == 0 and perf_counter() >= deadline:
                # Only publish a complete update: no suit/order bias from partial iteration.
                total = sum(weights.values())
                return RangeState(state.node.id, {c: w/total for c, w in weights.items()},
                                  min(.75, updates*.1), updates, False, None, calibration)
            if street == "preflop":
                likelihood = class_likelihood[COMBO_CLASSES[combo]]
            else:
                f = features(combo, board)
                score = f.strength
                draw = any(d in f.draws for d in ("FLUSH_DRAW", "OESD", "COMBO_DRAW"))
                aggressive_action = action in AGGRESSIVE and not (
                    cell is not None and observed[1] == "call")
                if aggressive_action:
                    threshold = .57 + .12*min(2, ratio) - adjustment
                    bluff = .055 if street == "river" else .09
                    likelihood = bluff + .85*sigmoid((score-threshold)*12)
                    if draw and street != "river":
                        likelihood = max(likelihood, .42)
                elif action == "call" or cell is not None and observed[1] == "call":
                    likelihood = .06 + .75*sigmoid((score-.40-.10*min(2, ratio))*12)
                    if draw:
                        likelihood = max(likelihood, .55)
                else:
                    likelihood = .78-.40*sigmoid((score-.72)*12)
            weights_before = weights[combo]
            new[combo] = max(1e-12, weights_before*likelihood)
            if likelihoods is not None:
                likelihoods[combo] = likelihood
        if cell is not None:
            adjusted = _calibrated_likelihoods(weights, likelihoods, cell, observed[1], deadline)
            if adjusted is None or perf_counter() >= deadline:
                total = sum(weights.values())
                reason("unsafe_or_deadline")
                return RangeState(state.node.id, {c: w/total for c, w in weights.items()},
                                  min(.75, updates*.1), updates, False, None, calibration)
            new = {c: max(1e-12, weights[c]*p) for c, p in adjusted.items()}
            if perf_counter() >= deadline:
                total = sum(weights.values())
                reason("unsafe_or_deadline")
                return RangeState(state.node.id, {c: w/total for c, w in weights.items()},
                                  min(.75, updates*.1), updates, False, None, calibration)
            calibration["applied"] = min(255, calibration["applied"]+1)
        weights = new
        updates += 1
    total = sum(weights.values())
    return RangeState(state.node.id, {c: w/total for c, w in weights.items()},
                      min(.75, updates*.1), updates, True, river_components, calibration)


def action_size_ratio(previous, entry):
    """Shared ledger convention; calls retain the facing bet-size evidence."""
    return ledger([*previous, entry])[-1].size_ratio


@dataclass(frozen=True)
class EquityResult:
    equity: float
    samples: int
    stderr: float
    exact: bool
    nuts: bool
    method: str


def equity(state, villain, rng, deadline, max_samples=900, force_mc=False):
    combos, cumulative = [], []
    total = 0.0
    for combo, weight in villain.weights.items():
        total += weight
        combos.append(combo)
        cumulative.append(total)
    if not combos:
        return EquityResult(.5, 0, .5, False, False, "empty_range")
    known = set(state.hero_cards+state.board)
    deck = [c for c in DECK if c not in known]
    win, n, hero_rank = 0.0, 0, None
    if len(state.board) == 5 and not force_mc:
        hero_rank = evaluate(state.hero_cards+state.board)
        weighted, mass, best = 0.0, 0.0, hero_rank
        for i, (combo, weight) in enumerate(villain.weights.items()):
            if i % 32 == 0 and perf_counter() >= deadline:
                break
            other = evaluate(combo+state.board)
            best = max(best, other)
            weighted += weight*(1.0 if hero_rank > other else .5 if hero_rank == other else 0)
            mass += weight
        else:
            return EquityResult(weighted/mass, len(combos), 0, True, hero_rank == best,
                                "river_exact_vs_heuristic_range")
        # Do not use a biased prefix if the deadline prevents full enumeration.
    while n < max_samples and perf_counter() < deadline:
        combo = combos[min(len(combos)-1, bisect_left(cumulative, rng.random()*total))]
        runout = rng.sample([c for c in deck if c not in combo], 5-len(state.board))
        board = state.board+tuple(runout)
        a = hero_rank or evaluate(state.hero_cards+board)
        b = evaluate(combo+board)
        win += 1 if a > b else .5 if a == b else 0
        n += 1
    value = win/n if n else .5
    return EquityResult(value, n, sqrt(value*(1-value)/max(1, n)), False, False,
                        "monte_carlo" if n else "budget_fallback")
