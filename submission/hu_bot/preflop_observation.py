"""Completed-hand, public-action opportunities for the first three HU nodes.

The counts are local to a match. A size key is an actual raise-to amount in
chips, so an opponent's 3.5BB open cannot contaminate the 2.25BB sample.
"""
from collections import Counter
from dataclasses import dataclass
from math import exp, lgamma, log, log1p

from .history import ledger


def depth_bucket(depth_bb):
    if depth_bb is None:
        return "unknown"
    for target, tolerance in ((100, 5), (75, 5), (50, 5), (30, 4)):
        if abs(depth_bb - target) <= tolerance:
            return f"{target}bb"
    return "other"


@dataclass(frozen=True)
class PreflopNodeKey:
    node: str
    facing_raise_to: int
    big_blind: int
    depth: str
    ante: int


def _outcome(event, node):
    action = event.action
    if action == "all_in":
        action = "raise" if event.amount_to > event.table_bet else "call"
    if action == "raise":
        return "raise"
    if node == "BTN_FIRST_IN" and action == "call":
        return "limp"
    if node == "BB_VS_LIMP" and action == "check":
        return "check"
    if node in {"BB_VS_OPEN", "BTN_VS_ISO", "BTN_VS_3BET"} and action in {"fold", "call"}:
        return action
    if node == "BTN_FIRST_IN" and action == "fold":
        return "fold"
    return None


def opportunities(history, dealer_seat, big_blind, depth_bb=None):
    """Return (observations, reason). Reject ambiguous/incomplete preflop lines.

    A round_result is the completion signal. We still require both blinds and
    each requested first response; a timeout or unknown action rejects the hand.
    No board, showdown, or future action enters a node's classification.
    """
    if big_blind <= 0 or dealer_seat not in (0, 1):
        return (), "invalid_rules"
    posts = {e.get("action"): e for e in history if e.get("phase", "preflop") == "preflop"
             and str(e.get("action", "")).startswith("post_")}
    if ("post_small_blind" not in posts or "post_big_blind" not in posts
            or int(posts["post_small_blind"].get("seat", -1)) != dealer_seat
            or int(posts["post_big_blind"].get("seat", -1)) != 1-dealer_seat):
        return (), "missing_or_wrong_blinds"
    preflop = [e for e in history if e.get("phase", "preflop") == "preflop"
               and not str(e.get("action", "")).startswith("post_")]
    if not preflop or any(e.get("is_timeout") for e in preflop):
        return (), "incomplete_or_timeout"
    if int(preflop[0].get("seat", -1)) != dealer_seat:
        return (), "wrong_first_actor"
    events = [e for e in ledger(history, big_blind) if e.street == "preflop"
              and not e.action.startswith("post_")]
    if len(events) != len(preflop):
        return (), "history_mismatch"
    depth = depth_bucket(depth_bb)
    ante = sum(int(e.get("amount", 0)) for e in history
               if e.get("phase", "preflop") == "preflop" and e.get("action") == "post_ante")
    result = []
    first = events[0]
    first_out = _outcome(first, "BTN_FIRST_IN")
    if first_out is None:
        return (), "unknown_first_action"
    result.append((PreflopNodeKey("BTN_FIRST_IN", 0, big_blind, depth, ante),
                   first_out, first.amount_to if first_out == "raise" else 0,
                   dealer_seat))
    if first_out == "fold":
        return tuple(result), None
    if len(events) < 2 or events[1].seat != 1-dealer_seat:
        return (), "missing_bb_response"
    second = events[1]
    node = "BB_VS_LIMP" if first_out == "limp" else "BB_VS_OPEN"
    second_out = _outcome(second, node)
    if second_out is None:
        return (), "unknown_bb_response"
    facing = first.amount_to if node == "BB_VS_OPEN" else big_blind
    result.append((PreflopNodeKey(node, facing, big_blind, depth, ante),
                   second_out, second.amount_to if second_out == "raise" else 0,
                   1-dealer_seat))
    if second_out == "raise":
        if len(events) < 3 or events[2].seat != dealer_seat:
            return (), "missing_btn_reraise_response"
        response_node = "BTN_VS_ISO" if node == "BB_VS_LIMP" else "BTN_VS_3BET"
        response = events[2]
        response_out = _outcome(response, response_node)
        if response_out is None:
            return (), "unknown_btn_response"
        result.append((PreflopNodeKey(response_node, second.amount_to, big_blind, depth, ante),
                       response_out, response.amount_to if response_out == "raise" else 0,
                       dealer_seat))
    return tuple(result), None


class OpportunityLedger:
    def __init__(self):
        self.counts = {}
        self.raise_sizes = {}
        self.skipped = Counter()
        # Match-local exact IDs preserve idempotence for arbitrarily long matches.
        self._seen = set()

    def observe_hand(self, hand_id, result, hero_seat, dealer_seat, big_blind,
                     depth_bb=None):
        if hand_id in self._seen:
            self.skipped["duplicate"] += 1
            return 0
        if not isinstance(result, dict) or not isinstance(result.get("action_history"), list):
            self.skipped["incomplete_result"] += 1
            return 0
        observations, reason = opportunities(result["action_history"], dealer_seat,
                                              big_blind, depth_bb)
        self._seen.add(hand_id)
        if reason:
            self.skipped[reason] += 1
            return 0
        count = 0
        for key, outcome, raise_to, actor in observations:
            if actor != hero_seat:
                self.counts.setdefault(key, Counter())[outcome] += 1
                if outcome == "raise":
                    self.raise_sizes.setdefault(key, Counter())[raise_to] += 1
                count += 1
        return count

    def summary(self):
        return {"cells": len(self.counts),
                "opportunities": sum(sum(cell.values()) for cell in self.counts.values()),
                "skipped": dict(self.skipped)}


def _beta_cf(a, b, x):
    # Continued fraction for regularized incomplete beta; no runtime SciPy.
    qab, qap, qam = a+b, a+1, a-1
    c, d = 1.0, 1.0-qab*x/qap
    if abs(d) < 1e-30:
        d = 1e-30
    d = 1.0/d
    h = d
    for m in range(1, 201):
        m2 = 2*m
        aa = m*(b-m)*x/((qam+m2)*(a+m2))
        d = 1+aa*d
        c = 1+aa/c
        if abs(d) < 1e-30:
            d = 1e-30
        if abs(c) < 1e-30:
            c = 1e-30
        d = 1/d
        h *= d*c
        aa = -(a+m)*(qab+m)*x/((a+m2)*(qap+m2))
        d = 1+aa*d
        c = 1+aa/c
        if abs(d) < 1e-30:
            d = 1e-30
        if abs(c) < 1e-30:
            c = 1e-30
        d = 1/d
        delta = d*c
        h *= delta
        if abs(delta-1) < 3e-10:
            break
    return h


def _beta_cdf(x, a, b):
    if x <= 0:
        return 0.0
    if x >= 1:
        return 1.0
    factor = exp(lgamma(a+b)-lgamma(a)-lgamma(b)+a*log(x)+b*log1p(-x))
    if x < (a+1)/(a+b+2):
        return factor*_beta_cf(a, b, x)/a
    return 1-factor*_beta_cf(b, a, 1-x)/b


def _beta_quantile(p, a, b):
    lo, hi = 0.0, 1.0
    for _ in range(45):
        mid = (lo+hi)/2
        if _beta_cdf(mid, a, b) < p:
            lo = mid
        else:
            hi = mid
    return (lo+hi)/2


class OpponentPosterior:
    def __init__(self, prior_weight=20.0):
        if prior_weight <= 0:
            raise ValueError("prior_weight must be positive")
        self.prior_weight = float(prior_weight)

    def predict(self, counts, prior, source="v1.5.2_heuristic_1326"):
        labels = tuple(prior)
        if not labels or any(v <= 0 for v in prior.values()) or abs(sum(prior.values())-1) > 1e-8:
            raise ValueError("Positive normalized multinomial prior required")
        if any(v < 0 for v in counts.values()) or set(counts)-set(labels):
            raise ValueError("Invalid mutually exclusive counts")
        n = sum(counts.values())
        concentration = self.prior_weight+n
        alpha = {key: self.prior_weight*prior[key]+counts.get(key, 0) for key in labels}
        mean = {key: alpha[key]/concentration for key in labels}
        intervals = {key: [_beta_quantile(.025, alpha[key], concentration-alpha[key]),
                           _beta_quantile(.975, alpha[key], concentration-alpha[key])]
                     for key in labels}
        return {"n": n, "counts": {key: counts.get(key, 0) for key in labels},
                "mean": mean, "interval_95": intervals, "prior": prior,
                "prior_weight": self.prior_weight, "prior_source": source}
