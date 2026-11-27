"""Bounded default-on 100/60BB no-ante HU preflop range candidate."""
from functools import lru_cache
import hashlib
import json
from pathlib import Path

from .core import Policy, street_bets
from .poker import hand_class
from .preflop import CLASS_INDEX, TABLE_SHA256, bracket

RANGE_SPEC_SHA256 = hashlib.sha256(
    Path(__file__).with_name("range_spec_v183.json").read_bytes()).hexdigest()


@lru_cache(maxsize=1)
def _spec():
    path = Path(__file__).with_name("range_spec_v183.json")
    data = json.loads(path.read_text(encoding="utf-8"))
    if data["old_table_sha256"] != TABLE_SHA256:
        raise ValueError("RangeSpec was built against another preflop chart")
    return data


def _node(s):
    events = [e for e in s.action_history if e.get("phase", "preflop") == "preflop"
              and not e["action"].startswith("post_")]
    shape = [(e["seat"], e["action"]) for e in events]
    btn, bb = s.dealer_seat, 1-s.dealer_seat
    if s.position == "BTN" and not shape:
        return "BTN_FIRST_IN", 0
    if s.position == "BB" and shape == [(btn, "call")]:
        return "BB_VS_LIMP", 1
    if s.position == "BB" and shape == [(btn, "raise")]:
        return "BB_VS_OPEN", events[0]["amount"]/s.big_blind
    if s.position == "BTN" and shape == [(btn, "call"), (bb, "raise")]:
        return "BTN_VS_ISO", events[1]["amount"]/s.big_blind
    if s.position == "BTN" and shape == [(btn, "raise"), (bb, "raise")]:
        return "BTN_VS_3BET", events[1]["amount"]/s.big_blind
    if s.position == "BB" and shape == [(btn, "call"), (bb, "raise"),
                                         (btn, "raise")]:
        return "BB_VS_LIMP_RERAISE", events[2]["amount"]/s.big_blind
    if s.position == "BB" and shape == [(btn, "raise"), (bb, "raise"),
                                         (btn, "raise")]:
        return "BB_VS_4BET", events[2]["amount"]/s.big_blind
    return None


def _history_price_consistent(s, context):
    """Require the observed preflop ledger to agree with authoritative call price.

    normalize() repairs incomplete ledgers with to_call. Comparing only its
    repaired bets would therefore accept a stale cheap open as the current
    price. Reconstruct the ledger independently before using the new table.
    """
    events = [e for e in s.action_history
              if e.get("phase", "preflop") == "preflop"]
    btn, bb = s.dealer_seat, 1-s.dealer_seat
    if (len(events) < 2 or events[0].get("action") != "post_small_blind" or
            events[0].get("seat") != btn or
            events[1].get("action") != "post_big_blind" or
            events[1].get("seat") != bb):
        return False
    try:
        small, big = int(events[0]["amount"]), int(events[1]["amount"])
        historic_bets = street_bets(events, "preflop")
        hero_bet = historic_bets.get(s.hero_seat)
        villain_bet = historic_bets.get(1-s.hero_seat)
    except (KeyError, TypeError, ValueError):
        return False
    if (not 0 < small < big or big != s.big_blind or
            hero_bet != s.hero_bet or villain_bet != s.villain_bet or
            s.pot != hero_bet + villain_bet or
            s.villain_bet-s.hero_bet != s.to_call or
            any(e["action"].startswith("post_") for e in events[2:])):
        return False
    if context == "BTN_FIRST_IN":
        return (len(events) == 2 and s.hero_bet == small and
                s.villain_bet == big and s.to_call == big-small and
                "call" in s.valid_actions)
    if context == "BB_VS_LIMP":
        return (len(events) == 3 and s.hero_bet == big and
                s.villain_bet == big and s.to_call == 0 and
                "check" in s.valid_actions)
    if context == "BB_VS_OPEN":
        return (len(events) == 3 and s.hero_bet == big and
                s.villain_bet == int(events[2]["amount"]) and
                s.to_call == s.villain_bet-big and
                "call" in s.valid_actions)
    return False


def candidate_status(s, include_research=False):
    """Return candidate policy and a traceable application or skip status.

    v1.7's reraised prices were indexed by the initial open. The actual
    facing raise-to is used above; these uncalibrated reraised branches fall
    back to the frozen chart, including large 3-bets and 4-bets.
    """
    if (s.street != "preflop" or s.num_players != 2 or
            any(e["action"] == "post_ante" and e["amount"] > 0
                for e in s.action_history) or
            not 50 <= s.effective_stack_bb <= 100 or
            s.villain_stack == 0 or s.to_call >= s.hero_stack):
        return None, "out_of_scope"
    node = _node(s)
    if node is None:
        return None, "out_of_scope"
    context, price = node
    # New-seed checks exclude 100BB first-in, 60BB vs limp and 2.5BB opens.
    # A separate depth sweep bounds the promoted 60BB table around its anchor.
    depth = s.effective_stack_bb
    if context == "BTN_FIRST_IN" and not 50 <= depth <= 70:
        return None, "out_of_scope"
    if context == "BB_VS_LIMP" and not (90 <= depth <= 100 or
                                       include_research and 55 <= depth <= 65):
        return None, "out_of_scope"
    if context == "BB_VS_OPEN":
        promoted = ((2 <= price <= 2.05 and 50 <= depth <= 70) or
                    (2.05 < price <= 2.25 and 55 <= depth <= 65))
        research = include_research and abs(price-2.5) < 1e-9 and 55 <= depth <= 65
        if not (promoted or research):
            return None, "out_of_scope"
    if context not in {"BTN_FIRST_IN", "BB_VS_LIMP", "BB_VS_OPEN"}:
        return None, "out_of_scope"
    if not _history_price_consistent(s, context):
        return None, "history_price_mismatch"
    data = _spec()
    anchors = data["price_anchors_by_context"][context]
    if not anchors[0] <= price <= anchors[-1]:
        return None, "out_of_scope"
    dlo, dhi, dw = bracket(s.effective_stack_bb,
                           sorted(data["depth_anchors_bb"]))
    plo, phi, pw = bracket(price, anchors)
    labels = data["actions_by_context"][context]
    index = CLASS_INDEX[hand_class(s.hero_cards)]
    weights = {label: 0.0 for label in labels}
    for depth, dweight in ((dlo, 1-dw), (dhi, dw)):
        for opening, pweight in ((plo, 1-pw), (phi, pw)):
            row = data["tables"][f"{context}:{depth}:{opening}"][index]
            for label, value in zip(labels, row):
                weights[label] += dweight*pweight*value
    return Policy(weights, f"default_range_v183:{context}").normalized(), "applied"


def candidate_policy(s, include_research=False):
    return candidate_status(s, include_research)[0]
