"""Full-combo v2.2 preflop audit on real ChipZen GameState objects.

This is an offline engineering receipt, not a solver or match-strength test.
Every row uses one of the 1,326 legal hole-card combinations, consistent
raise-to history, integer chip amounts, and the same no-ante 100BB stacks.
"""

from __future__ import annotations

from collections import Counter, defaultdict
import itertools
import json
from pathlib import Path
import sys


CANDIDATE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(CANDIDATE / "submission"))

from chipzen import GameState  # noqa: E402
from chipzen.models import Card  # noqa: E402
from hu_bot.poker import DECK, hand_class  # noqa: E402
from hu_bot.v2_runtime import V2Runtime  # noqa: E402


BLINDS = [
    {"seat": 0, "action": "post_small_blind", "amount": 50,
     "phase": "preflop", "is_timeout": False},
    {"seat": 1, "action": "post_big_blind", "amount": 100,
     "phase": "preflop", "is_timeout": False},
]
COMBOS = list(itertools.combinations(DECK, 2))


def event(seat, action, amount):
    return {"seat": seat, "action": action, "amount": amount,
            "phase": "preflop", "is_timeout": False}


def make_case(context, cards, depth_bb, open_to=0, three_to=0, four_to=0,
              hand_number=1):
    stack = depth_bb * 100
    if context == "BTN_FIRST_IN":
        hero_seat, history, pot, to_call = 0, BLINDS, 150, 50
        hero_stack, villain_stack = stack - 50, stack - 100
        min_raise = 200
    elif context == "BB_VS_OPEN":
        hero_seat, history = 1, BLINDS + [event(0, "raise", open_to)]
        pot, to_call = 100 + open_to, open_to - 100
        hero_stack, villain_stack = stack - 100, stack - open_to
        min_raise = 2 * open_to - 100
    elif context == "BTN_VS_3BET":
        hero_seat = 0
        history = BLINDS + [event(0, "raise", open_to), event(1, "raise", three_to)]
        pot, to_call = open_to + three_to, three_to - open_to
        hero_stack, villain_stack = stack - open_to, stack - three_to
        min_raise = 2 * three_to - open_to
    elif context == "BB_VS_4BET":
        hero_seat = 1
        history = (BLINDS + [event(0, "raise", open_to), event(1, "raise", three_to),
                             event(0, "raise", four_to)])
        pot, to_call = open_to + three_to + four_to, four_to - three_to
        hero_stack, villain_stack = stack - three_to, stack - four_to
        min_raise = 2 * four_to - three_to
    else:
        raise ValueError(context)
    max_raise = stack
    valid_actions = ["fold", "call"]
    if min_raise <= max_raise:
        valid_actions.append("raise")
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


def run_row(runtime, context, combo, depth_bb, open_to=0, three_to=0, four_to=0, hand_number=1):
    state = make_case(context, combo, depth_bb, open_to, three_to, four_to, hand_number)
    action = runtime.decide(state)
    trace = runtime.last_trace
    legal = action.action in state.valid_actions
    if action.action == "raise":
        legal = legal and state.min_raise <= action.amount <= state.max_raise
    node = trace.get("node_key", {})
    candidates = trace.get("preflop_candidates") or {}
    return {
        "context": context,
        "class": hand_class(combo),
        "combo": list(combo),
        "depth_bb": depth_bb,
        "open_to": open_to,
        "three_bet_to": three_to,
        "four_bet_to": four_to,
        "action": action.to_dict(),
        "legal": legal,
        "policy_reason": trace.get("policy", {}).get("reason"),
        "strength": trace.get("policy", {}).get("strength_proxy"),
        "fallback_reason": trace.get("fallback_reason"),
        "ledger_status": trace.get("ledger", {}).get("status"),
        "node_id": node.get("node_id"),
        "price_key": node.get("price_key"),
        "off_grid": node.get("off_grid"),
        "candidate_actions": candidates.get("actions", []),
        "candidate_raise_to": candidates.get("raise_to", []),
    }


def configs(depth_bb=100):
    for open_to in (200, 225, 250, 260):
        yield "BB_VS_OPEN", {"open_to": open_to}
    for three_to in (700, 800, 900, 1000):
        yield "BTN_VS_3BET", {"open_to": 250, "three_to": three_to}
        yield "BB_VS_4BET", {"open_to": 250, "three_to": three_to,
                              "four_to": round(2.2 * three_to)}


def counter_dict(values):
    counts = Counter(values)
    return {str(key): value for key, value in sorted(counts.items(), key=lambda item: str(item[0]))}


def summarize(rows):
    grouped = defaultdict(list)
    by_class = defaultdict(Counter)
    for row in rows:
        grouped[(row["context"], row["open_to"], row["three_bet_to"], row["four_bet_to"])].append(row)
        by_class[(row["context"], row["open_to"], row["three_bet_to"],
                  row["four_bet_to"], row["class"])] += Counter({row["action"]["action"]: 1})
    summary = {}
    for key, group in sorted(grouped.items(), key=lambda item: str(item[0])):
        label = ":".join(map(str, key))
        summary[label] = {
            "rows": len(group),
            "legal_rows": sum(row["legal"] for row in group),
            "fallback_rows": sum(bool(row["fallback_reason"]) for row in group),
            "ledger_status": counter_dict(row["ledger_status"] for row in group),
            "actions": counter_dict(row["action"]["action"] for row in group),
            "raise_to": counter_dict(row["action"].get("amount") for row in group
                                      if row["action"]["action"] == "raise"),
            "policy_reasons": counter_dict(row["policy_reason"] for row in group),
            "off_grid": sum(bool(row["off_grid"]) for row in group),
            "candidate_raise_to": counter_dict(
                tuple(row["candidate_raise_to"]) for row in group),
            "class_action_counts": {
                hand_class_label: dict(sorted(counts.items()))
                for (ctx, open_to, three_to, four_to, hand_class_label), counts in by_class.items()
                if (ctx, open_to, three_to, four_to) == key
            },
        }
    return summary


def main():
    runtime = V2Runtime()
    runtime.reset("v2.2-preflop-1326", 100, 2000)
    rows = []
    hand_number = 220000
    for combo in COMBOS:
        rows.append(run_row(runtime, "BTN_FIRST_IN", combo, 100, hand_number=hand_number))
        hand_number += 1
    for context, values in configs():
        for combo in COMBOS:
            rows.append(run_row(runtime, context, combo, 100, hand_number=hand_number,
                                **values))
            hand_number += 1
    off_grid_rows = []
    for context, values in (
            ("BB_VS_OPEN", {"open_to": 240}),
            ("BTN_VS_3BET", {"open_to": 250, "three_to": 750}),
            ("BB_VS_4BET", {"open_to": 250, "three_to": 700, "four_to": 1800})):
        for combo in COMBOS:
            off_grid_rows.append(run_row(runtime, context, combo, 100,
                                         hand_number=hand_number, **values))
            hand_number += 1
    class_weights = counter_dict(hand_class(combo) for combo in COMBOS)
    output = {
        "candidate": str(CANDIDATE),
        "version": "2.2.2",
        "game": {"big_blind": 100, "ante": 0, "depth_bb": 100,
                 "combo_count": len(COMBOS), "class_count": len(class_weights)},
        "combo_class_weights": class_weights,
        "rows": len(rows),
        "legal_rows": sum(row["legal"] for row in rows),
        "fallback_rows": sum(bool(row["fallback_reason"]) for row in rows),
        "contexts": counter_dict(row["context"] for row in rows),
        "nodes": counter_dict(row["node_id"] for row in rows),
        "summary": summarize(rows),
        "off_grid_summary": summarize(off_grid_rows),
        "btn_first_in_sentinels": [
            row for row in rows
            if row["context"] == "BTN_FIRST_IN"
            and row["class"] in {"K4o", "97o", "Q8o", "72o", "32o", "AA"}
        ],
        "bb_open_sentinels": [
            row for row in rows
            if row["context"] == "BB_VS_OPEN"
            and row["open_to"] in {200, 250}
            and row["class"] in {"Q8o", "K4o", "K8o", "A2o", "J8o", "97o",
                                  "Q2o", "J4o", "T4o", "94o"}
        ],
        "profile_comparisons": {
            "normal": "not opponent-modelled in v2.2 baseline",
            "wide_call": "not opponent-modelled in v2.2 baseline",
            "strong_iso": "not opponent-modelled in v2.2 baseline",
            "linear_3bet": "not opponent-modelled in v2.2 baseline",
            "strong_4bet": "not opponent-modelled in v2.2 baseline",
            "reverse": "not opponent-modelled in v2.2 baseline",
        },
        "rows_sample": rows[:12],
    }
    target = CANDIDATE / "reports" / "v2-preflop-1326-audit-v2.2.2.json"
    target.write_text(json.dumps(output, indent=2, sort_keys=True), encoding="utf-8")
    print(json.dumps(output, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
