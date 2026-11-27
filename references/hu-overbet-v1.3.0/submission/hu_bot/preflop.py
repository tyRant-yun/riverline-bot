"""Explicit 169-class grid lookup with continuous depth/price interpolation."""
from bisect import bisect_right
import hashlib
import json
from pathlib import Path

from .core import Policy
from .poker import hand_class

_BYTES = Path(__file__).with_name("preflop_tables.json").read_bytes()
DATA = json.loads(_BYTES)
TABLE_SHA256 = hashlib.sha256(_BYTES).hexdigest()
CLASS_INDEX = {h: i for i, h in enumerate(DATA["classes"])}


def bracket(value, anchors):
    if value <= anchors[0]:
        return anchors[0], anchors[0], 0.0
    if value >= anchors[-1]:
        return anchors[-1], anchors[-1], 0.0
    upper = bisect_right(anchors, value)
    lo, hi = anchors[upper-1], anchors[upper]
    return lo, hi, (value-lo)/(hi-lo)


def lookup(context, depth, price, klass):
    lo, hi, t = bracket(depth, DATA["depth_anchors_bb"])
    small, large, u = bracket(price, DATA["price_anchors"])
    index = CLASS_INDEX[klass]
    weights = dict.fromkeys(DATA["actions"], 0.0)
    for d, dw in ((lo, 1-t), (hi, t)):
        for p, pw in ((small, 1-u), (large, u)):
            row = DATA["tables"][f"{context}:{d}:{p}"][index]
            for action, value in zip(DATA["actions"], row):
                weights[action] += dw*pw*value
    return Policy(weights, f"table:{context}:{klass}").normalized()


def context_for_state(s):
    if s.to_call and (s.villain_stack == 0 or s.to_call >= s.hero_stack):
        return "VS_JAM", 2.0
    if s.node.raises == 0:
        return ("BTN_OPEN" if s.position == "BTN" else "BB_LIMP"), 2.0
    if s.node.raises == 1:
        return ("BB_OPEN" if s.position == "BB" else "BTN_ISOLATION"), s.villain_bet/s.big_blind
    previous_bet = s.hero_bet or s.big_blind
    return ("VS_3BET" if s.node.raises == 2 else "VS_4BET"), s.villain_bet/previous_bet


def preflop_policy(s):
    context, price = context_for_state(s)
    policy = lookup(context, s.effective_stack_bb, price, hand_class(s.hero_cards))
    weights = dict(policy.weights)
    # A >5BB open or a disproportionately large re-raise must not inherit a cheap call.
    if context not in {"BTN_OPEN", "BB_LIMP", "VS_JAM"}:
        severity = max(0.0, min(.85, (price-5)*.12))
        if s.to_call > .35*s.hero_stack and context in {"VS_3BET", "VS_4BET"}:
            severity = max(severity, .60)
        if hand_class(s.hero_cards) not in {"AA", "KK", "QQ", "AKs", "AKo"}:
            moved = weights.get("CALL", 0)*severity
            weights["CALL"] = weights.get("CALL", 0)-moved
            weights["FOLD"] = weights.get("FOLD", 0)+moved
    if context == "VS_JAM":
        # Deep reraised premiums are intentionally mixed; a very cheap price can recover calls.
        odds = s.to_call/max(1, s.pot+s.to_call)
        if odds < .20:
            relief = min(.6, (.20-odds)*4)
            moved = weights.get("FOLD", 0)*relief
            weights["FOLD"] = weights.get("FOLD", 0)-moved
            weights["CALL"] = weights.get("CALL", 0)+moved
    return Policy(weights, policy.reason).normalized()
