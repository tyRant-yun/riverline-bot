"""Independent high-frequency preflop action audit for the frozen v2.0.0.

The audit constructs real ChipZen GameState objects with integer chip amounts
that represent exact 2.00/2.25/2.60 BB prices at a 100-chip big blind. It
does not import developer fixtures or modify the candidate.
"""

from __future__ import annotations

from collections import Counter, defaultdict
import json
from pathlib import Path
import sys


CANDIDATE = Path(r"E:\hu bot\.work\hu-v2-strength-v2.1.0")
sys.path.insert(0, str(CANDIDATE / "submission"))

from chipzen import GameState  # noqa: E402
from chipzen.models import Card  # noqa: E402
from hu_bot.v2_bot import V2HUBot  # noqa: E402


RANKS = "23456789TJQKA"
SUITS = ("s", "h", "d", "c")
BLINDS = [
    {"seat": 0, "action": "post_small_blind", "amount": 50,
     "phase": "preflop", "is_timeout": False},
    {"seat": 1, "action": "post_big_blind", "amount": 100,
     "phase": "preflop", "is_timeout": False},
]


def card_class_specs():
    specs = []
    for i in range(len(RANKS) - 1, -1, -1):
        specs.append((RANKS[i] * 2, (f"{RANKS[i]}s", f"{RANKS[i]}h")))
        for j in range(i - 1, -1, -1):
            high, low = RANKS[i], RANKS[j]
            specs.append((high + low + "s", (f"{high}s", f"{low}s")))
            specs.append((high + low + "o", (f"{high}s", f"{low}h")))
    assert len(specs) == 169
    return specs


def event(seat, action, amount, phase="preflop"):
    return {"seat": seat, "action": action, "amount": amount,
            "phase": phase, "is_timeout": False}


def make_state(name, cards, depth_bb, hero_seat, history, pot, to_call,
               min_raise, max_raise, valid_actions, hand_number):
    stack = depth_bb * 100
    # The caller supplies the actual post-action remaining stacks through the
    # action line; this helper keeps the stack accounting explicit per node.
    if name.startswith("BTN_FIRST"):
        hero_stack, villain_stack = stack - 50, stack - 100
    elif name.startswith("BB_OPEN"):
        open_to = max(e["amount"] for e in history if e["action"] == "raise")
        hero_stack, villain_stack = stack - 100, stack - open_to
    else:
        open_to = next(e["amount"] for e in history if e["seat"] == 0 and e["action"] == "raise")
        three_to = max(e["amount"] for e in history if e["seat"] == 1 and e["action"] == "raise")
        hero_stack, villain_stack = stack - open_to, stack - three_to
    return GameState(
        hand_number=hand_number,
        phase="preflop",
        hole_cards=[Card.from_str(card) for card in cards],
        board=[],
        pot=pot,
        your_stack=hero_stack,
        opponent_stacks=[villain_stack],
        your_seat=hero_seat,
        dealer_seat=0,
        to_call=to_call,
        min_raise=min_raise,
        max_raise=max_raise,
        valid_actions=valid_actions,
        action_history=[dict(item) for item in history],
    )


def run_case(case, hand_number):
    bot = V2HUBot()
    bot.on_match_start({
        "match_id": f"independent-high-frequency-{hand_number}",
        "game_config": {"big_blind": 100},
        "turn_timeout_ms": 2000,
    })
    state_args = {key: value for key, value in case.items()
                  if key not in {"class_label", "price_bb", "hand_number"}}
    hero_seat = 1 if case["name"] == "BB_OPEN" else 0
    state = make_state(hand_number=hand_number, hero_seat=hero_seat, **state_args)
    bot.on_round_start({"state": {
        "hand_number": hand_number,
        "dealer_seat": 0,
        "big_blind": 100,
        "stacks": [100 * case["depth_bb"]] * 2,
    }})
    action = bot.decide(state)
    trace = bot.runtime.last_trace
    legal = action.action in state.valid_actions
    if action.action == "raise":
        legal = legal and state.min_raise <= action.amount <= state.max_raise
    return {
        "name": case["name"],
        "class": case["class_label"],
        "depth_bb": case["depth_bb"],
        "price_bb": case.get("price_bb"),
        "to_call": state.to_call,
        "node_id": trace["node_key"]["node_id"],
        "preflop_context": trace["snapshot"].get("preflop_context"),
        "facing_opponent_bet": trace["snapshot"].get("facing_opponent_bet"),
        "action": action.to_dict(),
        "legal": legal,
        "policy_reason": trace["policy"]["reason"],
        "strength": trace["policy"]["strength_proxy"],
        "fallback_reason": trace["fallback_reason"],
    }


def build_cases():
    cases = []
    hand_number = 31000
    for label, cards in card_class_specs():
        for depth in (100, 60, 20, 8):
            cases.append({
                "name": "BTN_FIRST_IN",
                "class_label": label,
                "cards": cards,
                "depth_bb": depth,
                "history": BLINDS,
                "pot": 150,
                "to_call": 50,
                "min_raise": 200,
                "max_raise": depth * 100,
                "valid_actions": ["fold", "call", "raise"],
                "hand_number": hand_number,
            })
            hand_number += 1
            for price_label, open_to in (("2.00", 200), ("2.25", 225), ("2.60", 260)):
                history = BLINDS + [event(0, "raise", open_to)]
                cases.append({
                    "name": "BB_OPEN",
                    "class_label": label,
                    "cards": cards,
                    "depth_bb": depth,
                    "price_bb": float(price_label),
                    "history": history,
                    "pot": 100 + open_to,
                    "to_call": open_to - 100,
                    "min_raise": 2 * open_to - 100,
                    "max_raise": depth * 100,
                    "valid_actions": ["fold", "call", "raise"],
                    "hand_number": hand_number,
                })
                hand_number += 1
            for three_to in (600, 700, 800):
                history = BLINDS + [event(0, "raise", 225), event(1, "raise", three_to)]
                cases.append({
                    "name": "BTN_3BET",
                    "class_label": label,
                    "cards": cards,
                    "depth_bb": depth,
                    "price_bb": three_to / 100,
                    "history": history,
                    "pot": 225 + three_to,
                    "to_call": three_to - 225,
                    "min_raise": 2 * three_to - 225,
                    "max_raise": depth * 100,
                    "valid_actions": ["fold", "call", "raise"]
                    if depth * 100 >= 2 * three_to - 225 else ["fold", "call"],
                    "hand_number": hand_number,
                })
                hand_number += 1
    return cases


def summarize(rows):
    by_key = defaultdict(Counter)
    for row in rows:
        key = (row["name"], row["depth_bb"], row.get("price_bb"))
        by_key[key][row["action"]["action"]] += 1
    first_in = [row for row in rows if row["name"] == "BTN_FIRST_IN"]
    bb_open = [row for row in rows if row["name"] == "BB_OPEN"]
    three_bet = [row for row in rows if row["name"] == "BTN_3BET"]
    masked = [row for row in bb_open
              if row["action"]["action"] == "call"
              and row["depth_bb"] >= 20
              and row["price_bb"] <= 3.5
              and row["strength"] > 0.640
              and row["depth_bb"] * 100 >= 2 * round(row["price_bb"] * 100) - 100]
    short_stack_masked = [row for row in bb_open
                          if row["action"]["action"] == "call"
                          and row["depth_bb"] < 20
                          and row["price_bb"] <= 3.5
                          and row["strength"] > 0.640]
    return {
        "candidate": str(CANDIDATE),
        "classes": 169,
        "rows": len(rows),
        "legal_rows": sum(row["legal"] for row in rows),
        "fallback_rows": sum(bool(row["fallback_reason"]) for row in rows),
        "btn_first_in": {
            "rows": len(first_in),
            "to_call_positive": sum(row["to_call"] > 0 for row in first_in),
            "node_ids": Counter(row["node_id"] for row in first_in),
            "contexts": Counter(row["preflop_context"] for row in first_in),
            "facing_opponent_bet": sum(row["facing_opponent_bet"] for row in first_in),
            "actions": Counter(row["action"]["action"] for row in first_in),
            "policy_reasons": Counter(row["policy_reason"] for row in first_in),
            "price_branch_reasons": sum(row["policy_reason"].startswith("price_aware_")
                                         or row["policy_reason"] == "premium_price_aware_3bet"
                                         for row in first_in),
        },
        "bb_open": {
            "rows": len(bb_open),
            "actions_by_price_depth": {
                f"{key[1]}BB/{key[2]:.2f}BB": dict(value)
                for key, value in sorted(by_key.items()) if key[0] == "BB_OPEN"
            },
            "policy_reasons": Counter(row["policy_reason"] for row in bb_open),
            "masked_call_candidates": len(masked),
            "short_stack_call_candidates": len(short_stack_masked),
            "raise_medium_or_more": sum(row["action"]["action"] == "raise"
                                         and row["policy_reason"] in {
                                             "open_price_aware_3bet",
                                             "premium_price_aware_3bet",
                                             "strong_price_aware_reraise"}
                                         for row in bb_open),
        },
        "btn_3bet": {
            "rows": len(three_bet),
            "actions_by_price_depth": {
                f"{key[1]}BB/{key[2]:.2f}BB": dict(value)
                for key, value in sorted(by_key.items()) if key[0] == "BTN_3BET"
            },
            "policy_reasons": Counter(row["policy_reason"] for row in three_bet),
        },
        "selected_rows": [
            row for row in rows
            if row["class"] in {"72o", "Q8o", "AKs", "AA"}
            and row["depth_bb"] in {100, 20}
            and row["name"] in {"BTN_FIRST_IN", "BB_OPEN", "BTN_3BET"}
            and (row.get("price_bb") in {None, 2.0, 2.25, 2.6, 6.0, 7.0, 8.0})
        ][:80],
    }


def main():
    rows = []
    for index, case in enumerate(build_cases()):
        rows.append(run_case(case, case["hand_number"]))
    print(json.dumps(summarize(rows), indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
