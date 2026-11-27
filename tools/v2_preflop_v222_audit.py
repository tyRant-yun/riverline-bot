"""Natural 1326-combo audit for v2.2.2 limp/iso and 3bet off-grid paths."""

from __future__ import annotations

from collections import Counter
import json
from pathlib import Path
import sys

CANDIDATE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(CANDIDATE / "submission"))
sys.path.insert(0, str(CANDIDATE / "tools"))

from chipzen import GameState  # noqa: E402
from chipzen.models import Card  # noqa: E402
from hu_bot.poker import hand_class  # noqa: E402
from hu_bot.v2_runtime import V2Runtime  # noqa: E402
from v2_preflop_1326_audit import BLINDS, COMBOS, event, run_row  # noqa: E402


def iso_state(combo, iso_to, hand_number):
    stack = 10000
    history = BLINDS + [event(0, "call", 50), event(1, "raise", iso_to)]
    return GameState(
        hand_number=hand_number, phase="preflop",
        hole_cards=[Card.from_str(card) for card in combo], board=[],
        pot=iso_to + 100, your_stack=stack - 50,
        opponent_stacks=[stack - iso_to], your_seat=0, dealer_seat=0,
        to_call=iso_to - 50, min_raise=2 * iso_to - 50,
        max_raise=stack, valid_actions=["fold", "call", "raise"],
        action_history=[dict(item) for item in history],
    )


def run_iso(runtime, combo, iso_to, hand_number):
    state = iso_state(combo, iso_to, hand_number)
    action = runtime.decide(state)
    trace = runtime.last_trace
    legal = action.action in state.valid_actions
    if action.action == "raise":
        legal = legal and state.min_raise <= action.amount <= state.max_raise
    node = trace.get("node_key", {})
    return {
        "class": hand_class(combo), "combo": list(combo), "iso_to": iso_to,
        "action": action.to_dict(), "legal": legal,
        "policy_reason": trace.get("policy", {}).get("reason"),
        "fallback_reason": trace.get("fallback_reason"),
        "context": trace.get("snapshot", {}).get("preflop_context"),
        "node_id": node.get("node_id"), "off_grid": node.get("off_grid"),
        "ledger_status": trace.get("ledger", {}).get("status"),
        "price_key": node.get("price_key"),
    }


def summarize(rows):
    return {
        "rows": len(rows),
        "actions": dict(sorted(Counter(row["action"]["action"] for row in rows).items())),
        "legal_rows": sum(bool(row["legal"]) for row in rows),
        "fallback_rows": sum(bool(row["fallback_reason"]) for row in rows),
        "off_grid_rows": sum(bool(row["off_grid"]) for row in rows),
        "ledger_status": dict(sorted(Counter(row["ledger_status"] for row in rows).items())),
        "policy_reasons": dict(sorted(Counter(row["policy_reason"] for row in rows).items())),
        "classes": dict(sorted(Counter(row["class"] for row in rows).items())),
    }


def main():
    runtime = V2Runtime()
    runtime.reset("v2.2.2-preflop-audit", 100, 2000)
    first_rows = [run_row(runtime, "BTN_FIRST_IN", combo, 100, hand_number=500000 + index)
                  for index, combo in enumerate(COMBOS)]
    natural_limp = [row for row in first_rows if row["action"]["action"] == "call"]
    limp_combos = [tuple(row["combo"]) for row in natural_limp]
    iso = {}
    for iso_to in (300, 500, 600):
        rows = [run_iso(runtime, combo, iso_to, 510000 + iso_to + index)
                for index, combo in enumerate(limp_combos)]
        iso[str(iso_to)] = summarize(rows)
        iso[str(iso_to)]["sample_rows"] = rows[:12]

    three_bet = {}
    for price_bb in (5.9, 6.0, 6.4, 6.5, 6.9, 7.0, 8.0, 9.0, 10.0,
                     10.1, 10.5, 10.6, 11.0, 11.1):
        three_to = round(price_bb * 100)
        rows = [run_row(runtime, "BTN_VS_3BET", combo, 100, open_to=250,
                        three_to=three_to, hand_number=520000 + index)
                for index, combo in enumerate(COMBOS)]
        sentinels = [row for row in rows
                     if row["class"] in {"97o", "65o", "65s", "AA", "AKs", "KJo"}]
        three_bet[f"{price_bb:.1f}BB"] = {
            **summarize(rows),
            "raise_to": sorted({row["action"].get("amount") for row in rows
                                 if row["action"]["action"] == "raise"}),
            "sentinels": sentinels,
        }

    output = {
        "candidate": str(CANDIDATE), "version": "2.2.2",
        "game": {"big_blind": 100, "ante": 0, "depth_bb": 100,
                 "combo_count": len(COMBOS)},
        "first_in": summarize(first_rows),
        "natural_limp_rows": len(natural_limp),
        "natural_limp_classes": dict(sorted(Counter(row["class"] for row in natural_limp).items())),
        "iso_by_actual_raise_to": iso,
        "btn_vs_3bet_by_actual_raise_to": three_bet,
        "limits": [
            "Natural iso rows are conditioned on this candidate's concrete BTN limp range.",
            "Opponent response and continuation Q remain model_limited.",
        ],
    }
    target = CANDIDATE / "reports" / "v2-preflop-v222-natural-audit.json"
    target.write_text(json.dumps(output, indent=2, sort_keys=True), encoding="utf-8")
    print(json.dumps(output, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
