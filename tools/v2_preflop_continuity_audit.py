"""Read-only 8.9/9.0/9.1/10.0BB BTN-vs-3bet continuity audit."""

from __future__ import annotations

from collections import Counter
import json
from pathlib import Path
import sys

CANDIDATE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(CANDIDATE / "submission"))
sys.path.insert(0, str(CANDIDATE / "tools"))

from v2_preflop_1326_audit import COMBOS, run_row  # noqa: E402
from hu_bot.v2_runtime import V2Runtime  # noqa: E402


def main():
    runtime = V2Runtime()
    runtime.reset("v2.2.1-continuity", 100, 2000)
    result = {}
    sentinel_rows = []
    hand_number = 321000
    rfi_rows = [run_row(runtime, "BTN_FIRST_IN", combo, 100,
                        hand_number=300000 + index)
                for index, combo in enumerate(COMBOS)]
    rfi_indices = [index for index, row in enumerate(rfi_rows)
                   if row["action"]["action"] == "raise"
                   and row["action"].get("amount") == 250]
    for three_to in (890, 900, 910, 1000):
        rows = [run_row(runtime, "BTN_VS_3BET", combo, 100, open_to=250,
                        three_to=three_to, hand_number=hand_number + index)
                for index, combo in enumerate(COMBOS)]
        reached_rows = [rows[index] for index in rfi_indices]
        conditional_actions = Counter(row["action"]["action"] for row in reached_rows)
        result[f"{three_to / 100:.2f}BB"] = {
            "rows": len(rows),
            "actions": dict(Counter(row["action"]["action"] for row in rows)),
            "rfi_raise_to_2p5_rows": len(reached_rows),
            "conditional_actions_given_rfi": dict(conditional_actions),
            "conditional_rates_given_rfi": {
                action: round(count / max(1, len(reached_rows)), 6)
                for action, count in sorted(conditional_actions.items())
            },
            "policy_reasons": dict(Counter(row["policy_reason"] for row in rows)),
            "off_grid_rows": sum(bool(row["off_grid"]) for row in rows),
            "legal_rows": sum(row["legal"] for row in rows),
            "fallback_rows": sum(bool(row["fallback_reason"]) for row in rows),
            "candidate_raise_to": sorted({tuple(row["candidate_raise_to"]) for row in rows}),
        }
        for combo in (("6s", "5s"), ("7s", "6s"), ("5s", "5h"), ("Qs", "8s")):
            sentinel_rows.append(run_row(runtime, "BTN_VS_3BET", combo, 100,
                                         open_to=250, three_to=three_to,
                                         hand_number=500000 + three_to))
        hand_number += len(COMBOS)
    output = {"candidate": str(CANDIDATE), "version": "2.2.1",
              "rfi_range_definition": "BTN_FIRST_IN raise-to=250 at 100BB, zero ante",
              "rfi_raise_to_2p5_rows": len(rfi_indices),
              "summary": result,
              "sentinels": sentinel_rows}
    target = CANDIDATE / "reports" / "v2-preflop-continuity-v2.2.1.json"
    target.write_text(json.dumps(output, indent=2, sort_keys=True), encoding="utf-8")
    print(json.dumps(output, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
