"""Evidence-gated IP river value sizing. Response identities remain uncertain.

This is a bounded conditional-EV comparison, not an equilibrium solver. Checking
in position closes river action; raises are scored as a bet-fold loss.
"""
from bisect import bisect_left, bisect_right
from math import exp, isfinite, sqrt
from time import perf_counter

from .core import Policy
from .history import ledger
from .poker import evaluate
from .responses import response_keys
from .river import bet_fold_ev, calibrated_rows
from .strategy import legalize


LABELS = ("BET_SMALL", "BET_MEDIUM", "BET_LARGE", "BET_OVERBET")
MIN_CONTEXT_OPPORTUNITIES = 48


def _sigmoid(x):
    return 1 / (1 + exp(max(-30., min(30., -x))))


def _wilson(success, n, upper):
    """One-sided 95% Wilson bound; treated as a stress setting, not a guarantee."""
    z = 1.645
    p = success / n
    centre = (p + z*z/(2*n)) / (1+z*z/n)
    half = z*sqrt(p*(1-p)/n+z*z/(4*n*n))/(1+z*z/n)
    return min(1., centre+half) if upper else max(0., centre-half)


def _evidence(model, pot_type, ratio):
    # The broad river/size parents are intentionally insufficient for promotion.
    key = response_keys(pot_type, False, ratio)[2]
    stats = getattr(model, "stats", {})
    fold, raise_ = stats.get(key+":fold"), stats.get(key+":raise")
    if (fold is None or raise_ is None or
            fold.opportunities < MIN_CONTEXT_OPPORTUNITIES or
            fold.opportunities != raise_.opportunities):
        return None
    n = fold.opportunities
    if not (0 <= fold.success <= n and 0 <= raise_.success <= n and
            fold.success + raise_.success <= n):
        return None
    f, r = fold.success/n, raise_.success/n
    return {"key": key, "n": n, "fold": f, "raise": r,
            "fold_lower": _wilson(fold.success, n, False),
            "raise_upper": _wilson(raise_.success, n, True)}


def _rows(combos, observed, stronger_continue, range_stress, deadline):
    """Use public hand strength for response propensity; preserve range mass.

    Aggregate response counts constrain totals. They cannot tell which private
    hands folded. The steeper continuation and range tilt are explicit stresses.
    """
    tilted = []
    for i, (mass, win, strength) in enumerate(combos):
        if i % 32 == 0 and perf_counter() >= deadline:
            raise TimeoutError
        # Bounded adverse reweighting of the *range*, independent of response.
        factor = (1.2-0.3*win) if range_stress else 1.
        tilted.append((mass*factor, win, strength))
    total = sum(row[0] for row in tilted)
    slope = 19. if stronger_continue else 10.
    prior = []
    for mass, win, strength in tilted:
        fold = .015+.97*_sigmoid((.48-strength)*slope)
        raise_ = .005+.20*_sigmoid((strength-.88)*25)
        raise_ = min(raise_, (1-fold)*.7)
        prior.append([mass/total, win, fold, 1-fold-raise_, raise_])
    # Exact empirical frequency targets, with response identities from the
    # uncertain strength heuristic. No hand-specific labels are inferred.
    result = calibrated_rows(prior, {"fold": observed[0], "raise": observed[1],
                                     "weight": 1.}, deadline)
    return result


def _score(rows, pot, bet):
    fold = sum(row[0]*row[2] for row in rows)
    call = sum(row[0]*row[3] for row in rows)
    raise_ = sum(row[0]*row[4] for row in rows)
    equity = sum(row[0]*row[1]*row[3] for row in rows)/call if call > 1e-12 else 0.
    return {"ev": bet_fold_ev(pot, bet, fold, call, raise_, equity),
            "fold": fold, "call": call, "raise": raise_, "called_equity": equity}


def plan_value(state, villain_range, hero_public, opponent_model, deadline, enabled=False):
    """Return (policy or None, diagnostics), atomically on an expired deadline.

    A CHECK policy is returned only after at least one context cell has enough
    actual opportunities. Otherwise None leaves the caller's baseline intact.
    """
    if not enabled:
        return None, {"complete": False, "reason": "disabled"}
    s = state
    if (s.street != "river" or len(s.board) != 5 or not s.in_position or s.to_call or
            s.node.street_raises or "check" not in s.valid_actions or
            "raise" not in s.valid_actions or s.num_players != 2):
        return None, {"complete": False, "reason": "unsupported_node"}
    river_actions = [e for e in s.action_history
                     if e.get("phase") == "river" and not e["action"].startswith("post_")]
    if (len(river_actions) != 1 or river_actions[0]["seat"] == s.hero_seat
            or river_actions[0]["action"] != "check"):
        return None, {"complete": False, "reason": "unconfirmed_terminal_check"}
    if (not villain_range.complete or not hero_public.complete or
            not villain_range.weights or not hero_public.weights):
        return None, {"complete": False, "reason": "incomplete_or_empty_range"}
    events = ledger(s.action_history, s.big_blind,
                    {s.hero_seat: s.hero_stack, 1-s.hero_seat: s.villain_stack})
    depths = [e.effective_depth_bb for e in events
              if e.street == "preflop" and not e.action.startswith("post_")]
    if not depths or not 60 <= depths[0] <= 125:
        return None, {"complete": False, "reason": "outside_60_125bb_start"}
    try:
        if perf_counter() >= deadline:
            raise TimeoutError
        # Most matches have no sufficiently populated exact response cell.
        # Check that before the two full river range evaluations.
        used, size_cells = set(), []
        for label in LABELS:
            if perf_counter() >= deadline:
                raise TimeoutError
            action, _ = legalize(label, s)
            if action.action != "raise" or action.amount in used:
                continue
            used.add(action.amount)
            bet = action.amount-s.hero_bet
            if bet <= 0 or bet > min(s.hero_stack, s.villain_stack):
                continue
            evidence = _evidence(opponent_model, s.node.pot_type, bet/max(1, s.pot))
            if evidence is not None:
                size_cells.append((label, action.amount, bet, evidence))
        if not size_cells:
            return None, {"complete": False, "reason": "insufficient_context_response_evidence"}
        board = set(s.board)
        known = board | set(s.hero_cards)
        public = []
        for i, (combo, mass) in enumerate(hero_public.weights.items()):
            if i % 32 == 0 and perf_counter() >= deadline:
                raise TimeoutError
            if (len(combo) != 2 or combo[0] == combo[1] or board.intersection(combo)
                    or not isfinite(mass) or mass < 0):
                return None, {"complete": False, "reason": "invalid_public_range"}
            if mass:
                public.append((evaluate(combo+s.board), mass))
        if not public:
            return None, {"complete": False, "reason": "empty_public_range"}
        public.sort()
        ranks, cumulative = [], [0.]
        for rank, mass in public:
            ranks.append(rank)
            cumulative.append(cumulative[-1]+mass)
        hero_rank = evaluate(s.hero_cards+s.board)
        combos = []
        for i, (combo, mass) in enumerate(villain_range.weights.items()):
            if i % 32 == 0 and perf_counter() >= deadline:
                raise TimeoutError
            if (len(combo) != 2 or combo[0] == combo[1] or known.intersection(combo)
                    or not isfinite(mass) or mass < 0):
                return None, {"complete": False, "reason": "invalid_villain_range"}
            if not mass:
                continue
            rank = evaluate(combo+s.board)
            left, right = bisect_left(ranks, rank), bisect_right(ranks, rank)
            strength = (cumulative[left]+cumulative[right])/(2*cumulative[-1])
            win = 1. if hero_rank > rank else .5 if hero_rank == rank else 0.
            combos.append((mass, win, strength))
        total = sum(row[0] for row in combos)
        if total <= 0:
            return None, {"complete": False, "reason": "empty_villain_range"}
        combos = [(m/total, w, strength) for m, w, strength in combos]
        check_ev = s.pot*sum(m*w for m, w, _ in combos)
        candidates = []
        for label, amount, bet, evidence in size_cells:
            if perf_counter() >= deadline:
                raise TimeoutError
            # No raise branch when the defender cannot make a legal increase.
            can_raise = bet < min(s.hero_stack, s.villain_stack)
            central = (evidence["fold"], evidence["raise"] if can_raise else 0.)
            pessimistic = (evidence["fold_lower"],
                           evidence["raise_upper"] if can_raise else 0.)
            if sum(pessimistic) > 1:
                pessimistic = (pessimistic[0], 1-pessimistic[0])
            scenarios = []
            for name, target, stronger, tilted in (
                    ("central", central, False, False),
                    ("strong_continue", central, True, False),
                    ("frequency_stress", pessimistic, True, False),
                    ("range_stress", pessimistic, True, True)):
                rows = _rows(combos, target, stronger, tilted, deadline)
                score = _score(rows, s.pot, bet)
                if not can_raise:
                    # All-in bet has no further raise opportunity; calibration
                    # to a zero raise target remains numerically near zero.
                    score["raise"] = 0.
                scenarios.append({"name": name, **score})
            worst = min(scenarios, key=lambda x: x["ev"])
            lowest_called = min(x["called_equity"] for x in scenarios)
            margin = max(.25*s.big_blind, .02*s.pot)
            eligible = lowest_called >= .5 and worst["ev"] >= check_ev+margin
            candidates.append({"label": label, "amount": amount, "bet": bet,
                               "evidence": evidence, "scenarios": scenarios,
                               "worst_ev": worst["ev"], "worst_scenario": worst["name"],
                               "min_called_equity": lowest_called, "margin_required": margin,
                               "eligible": eligible})
        if perf_counter() >= deadline:
            raise TimeoutError
        eligible = [c for c in candidates if c["eligible"]]
        best = max(eligible, key=lambda c: c["worst_ev"]) if eligible else None
        selected = best["label"] if best else "CHECK"
        return Policy({selected: 1.}, "river_evidence_value_ev"), {
            "complete": True, "selected": selected, "check_ev": check_ev,
            "depth_at_start_bb": depths[0], "candidates": candidates,
            "river_enumeration_complete": True,
            "response_identity": "uncertain_public_strength_heuristic",
            "aggregate_folds_do_not_identify_folded_hands": True,
            "raise_branch": "bet_fold", "check_branch": "terminal_showdown_ip"}
    except TimeoutError:
        return None, {"complete": False, "reason": "deadline_atomic_discard"}
