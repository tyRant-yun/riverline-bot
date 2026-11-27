"""Compare the v2.2.1 BTN-vs-3bet continuity slice with frozen v2.2.0.

This is a read-only same-state audit.  It deliberately runs each candidate in
its own subprocess so package imports cannot cross-contaminate the comparison.
"""

from __future__ import annotations

from collections import Counter, defaultdict
import json
from pathlib import Path
import subprocess
import sys


CANDIDATE = Path(__file__).resolve().parents[1]
PARENT = CANDIDATE.parent / "hu-v2-strength-v2.2.0"
PRICES = (8.9, 9.0, 9.1, 10.0)
REPRESENTATIVES = ("A2s", "KJo", "77", "A4s", "A5s", "TT", "65o",
                   "76o", "97o", "65s", "76s", "55", "Q8s", "AA",
                   "KK", "QQ", "AKs", "98s", "T9s")


def _rows(root: Path, three_bet_bb: float):
    code = r'''
import json
import sys
from pathlib import Path
root = Path(sys.argv[1])
three_to = int(round(float(sys.argv[2]) * 100))
sys.path.insert(0, str(root / "submission"))
sys.path.insert(0, str(root / "tools"))
from v2_preflop_1326_audit import COMBOS, run_row
from hu_bot.v2_runtime import V2Runtime
runtime = V2Runtime()
runtime.reset("v221-delta", 100, 2000)
rows = [run_row(runtime, "BTN_VS_3BET", combo, 100, open_to=250,
                 three_to=three_to, hand_number=230000 + i)
        for i, combo in enumerate(COMBOS)]
print(json.dumps(rows))
'''
    result = subprocess.run(
        [sys.executable, "-c", code, str(root), str(three_bet_bb)],
        cwd=root, check=True, capture_output=True, text=True,
    )
    return json.loads(result.stdout)


def _action(row):
    return row["action"]["action"]


def _summary(rows):
    transitions = Counter()
    by_class = defaultdict(Counter)
    for row in rows:
        transitions[_action(row)] += 1
        by_class[row["class"]][_action(row)] += 1
    return {
        "actions": dict(sorted(transitions.items())),
        "legal_rows": sum(bool(row["legal"]) for row in rows),
        "fallback_rows": sum(bool(row["fallback_reason"]) for row in rows),
        "off_grid_rows": sum(bool(row["off_grid"]) for row in rows),
        "by_class": {key: dict(sorted(value.items()))
                     for key, value in sorted(by_class.items())},
    }


def main():
    prices = {}
    for price in PRICES:
        old_rows = _rows(PARENT, price)
        new_rows = _rows(CANDIDATE, price)
        assert [row["combo"] for row in old_rows] == [row["combo"] for row in new_rows]
        delta = Counter(f"{_action(old)}->{_action(new)}"
                        for old, new in zip(old_rows, new_rows)
                        if _action(old) != _action(new))
        prices[f"{price:.2f}BB"] = {
            "old_v220": _summary(old_rows),
            "new_v221": _summary(new_rows),
            "changed_rows": sum(delta.values()),
            "action_delta": dict(sorted(delta.items())),
        }

    representatives = {}
    for cls in REPRESENTATIVES:
        representatives[cls] = {}
        for price in PRICES:
            old = prices[f"{price:.2f}BB"]["old_v220"]["by_class"].get(cls, {})
            new = prices[f"{price:.2f}BB"]["new_v221"]["by_class"].get(cls, {})
            representatives[cls][f"{price:.2f}BB"] = {
                "v220": old,
                "v221": new,
                "changed": old != new,
            }

    output = {
        "candidate": str(CANDIDATE),
        "parent": str(PARENT),
        "version": "2.2.1",
        "scope": "same 1326 BTN-vs-3bet states; no strategy-strength claim",
        "prices": prices,
        "representatives": representatives,
        "pdf_direction_audit": {
            "A2s": "PDF page 5 approximately 2% 4bet / 98% call; candidate keeps structural 4bet mix",
            "KJo": "PDF page 5 approximately 5% 4bet / 95% call; candidate strong profile routes to 4bet",
            "77": "PDF page 5 approximately 0% 4bet / 100% call; candidate strong profile routes to 4bet",
            "interpretation": "The v2.2.1 same-node range patch narrows these broad 4bets; the simplified result is still not a calibrated PDF/GTO match.",
        },
    }
    target = CANDIDATE / "reports" / "v2-preflop-v221-delta-audit.json"
    target.write_text(json.dumps(output, indent=2, sort_keys=True), encoding="utf-8")
    print(json.dumps(output, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
