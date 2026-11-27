"""100BB-oriented IP river check / bet-fold model. All likelihoods are heuristic.

Exact terminal payoffs, approximate opponent responses. Raises are charged the
full lost bet (conservative bet-fold scoring); this is not a solved river tree.
OOP checks, facing aggression, and nuts remain in the established policy.
"""
from bisect import bisect_left, bisect_right
from math import exp
from time import perf_counter

from .core import Policy
from .history import ledger
from .poker import evaluate
from .responses import evidence
from .strategy import legalize


def call_ev(pot, call, equity):
    return equity*(pot+call)-call


def bet_fold_ev(pot, bet, fold, call, raise_, called_equity):
    return fold*pot + call*(called_equity*(pot+2*bet)-bet) - raise_*bet


def _sigmoid(x):
    return 1/(1+exp(max(-30, min(30, -x))))


def calibrated_rows(rows, observed, deadline):
    """Rake joint (hand, response) masses, preserving each hand's marginal.

    rows = [range_mass, showdown_equity, fold_prob, call_prob, raise_prob].
    Targets blend model marginal and observed posterior, never treat aggregate
    folds as proof of the folded hand identities. Bounded iterations and atomic use.
    """
    if observed is None:
        return rows
    prior = [sum(r[0]*r[j+2] for r in rows) for j in range(3)]
    empirical = [observed["fold"], max(0, 1-observed["fold"]-observed["raise"]), observed["raise"]]
    weight = observed["weight"]
    target = [(1-weight)*p+weight*q for p, q in zip(prior, empirical)]
    factors = [1., 1., 1.]
    result = rows
    for _ in range(10):
        if perf_counter() >= deadline:
            raise TimeoutError
        result = []
        for mass, equity, *probs in rows:
            adjusted = [p*f for p, f in zip(probs, factors)]
            total = sum(adjusted)
            result.append([mass, equity, *(p/total for p in adjusted)])
        actual = [sum(r[0]*r[j+2] for r in result) for j in range(3)]
        if max(abs(a-b) for a, b in zip(actual, target)) < 1e-6:
            break
        factors = [f*t/max(1e-12, a) for f, t, a in zip(factors, target, actual)]
    return result


def plan(state, villain, hero_public, model, deadline, adapt=True):
    s = state
    if (s.street != "river" or not s.in_position or s.to_call or "raise" not in s.valid_actions
            or s.node.street_raises or not villain.complete or not hero_public.complete):
        return None, {"complete": False, "reason": "unsupported_node"}
    events = ledger(s.action_history, s.big_blind,
                    {s.hero_seat: s.hero_stack, 1-s.hero_seat: s.villain_stack})
    depths = [e.effective_depth_bb for e in events
              if e.street == "preflop" and not e.action.startswith("post_")]
    if not depths or not 60 <= depths[0] <= 125:
        return None, {"complete": False, "reason": "outside_60_125bb_start"}
    if not villain.weights or not hero_public.weights:
        return None, {"complete": False, "reason": "empty_range"}
    try:
        # A public-range rank percentile is a response feature, not opponent knowledge
        # of our private cards. Collision conditioning in this feature is approximate.
        public = []
        for i, (combo, mass) in enumerate(hero_public.weights.items()):
            if i % 32 == 0 and perf_counter() >= deadline:
                raise TimeoutError
            public.append((evaluate(combo+s.board), mass))
        public.sort()
        ranks, cumulative = [], [0.]
        for rank, mass in public:
            ranks.append(rank)
            cumulative.append(cumulative[-1]+mass)
        hero_rank = evaluate(s.hero_cards+s.board)
        combos = []
        total = sum(villain.weights.values())
        for i, (combo, mass) in enumerate(villain.weights.items()):
            if i % 32 == 0 and perf_counter() >= deadline:
                raise TimeoutError
            rank = evaluate(combo+s.board)
            left, right = bisect_left(ranks, rank), bisect_right(ranks, rank)
            strength = (cumulative[left]+cumulative[right])/(2*cumulative[-1])
            win = 1. if hero_rank > rank else .5 if hero_rank == rank else 0.
            combos.append((mass/total, win, strength))
        check_ev = sum(m*w*s.pot for m, w, _ in combos)
        candidates = [{"label": "CHECK", "ev": check_ev, "amount": 0, "eligible": True}]
        used = set()
        for label in ("BET_SMALL", "BET_MEDIUM", "BET_LARGE", "BET_OVERBET"):
            if perf_counter() >= deadline:
                raise TimeoutError
            action, _ = legalize(label, s)
            if action.action != "raise" or action.amount in used:
                continue
            used.add(action.amount)
            bet = action.amount-s.hero_bet
            ratio = bet/max(1, s.pot)
            can_raise = bet < min(s.hero_stack, s.villain_stack)
            rows = []
            for mass, win, strength in combos:
                continued = .03+.95*_sigmoid((strength-(.42+.22*ratio/(1+2*ratio)))*10)
                raised = (.005+.22*_sigmoid((strength-.88)*25)) if can_raise else 0.
                raised = min(raised, continued*.7)
                rows.append([mass, win, 1-continued, continued-raised, raised])
            observed = evidence(model, s.node.pot_type, False, ratio) if adapt else None
            if observed is not None and not can_raise:
                observed = {**observed, "raise": 0.}
            rows = calibrated_rows(rows, observed, deadline)
            fold = sum(r[0]*r[2] for r in rows)
            call = sum(r[0]*r[3] for r in rows)
            raise_ = sum(r[0]*r[4] for r in rows)
            ec = sum(r[0]*r[1]*r[3] for r in rows)/max(1e-12, call)
            ev = bet_fold_ev(s.pot, bet, fold, call, raise_, ec)
            # Do not invent new river bluffs from the heuristic population prior.
            # Existing baseline bluff mixes survive; new bluff-heavy targets need evidence.
            eligible = ec >= .5 or (observed is not None and
                         observed["fold_guard"] > bet/(s.pot+bet)+.03)
            candidates.append({"label": label, "amount": action.amount, "ev": ev,
                               "fold": fold, "call": call, "raise": raise_,
                               "called_equity": ec, "evidence": observed, "eligible": eligible})
        if perf_counter() >= deadline:
            raise TimeoutError
        best = max((c for c in candidates if c["eligible"]), key=lambda item: item["ev"])
        # Small model gains do not justify risking a bet; check wins close ties.
        if best["ev"]-check_ev < max(s.big_blind*.25, s.pot*.01):
            best = candidates[0]
        return Policy({best["label"]: 1}, "river_ip_response_ev"), {
            "complete": True, "model": "public-rank+hierarchical-response/bet-fold-v1",
            "depth_at_start_bb": depths[0], "selected": best["label"],
            "candidates": candidates, "raise_branch": "lose_bet_conservative_score"}
    except TimeoutError:
        return None, {"complete": False, "reason": "deadline_atomic_discard"}
