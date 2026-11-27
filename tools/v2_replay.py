"""Replay the developer's compact v2 coverage fixtures into a JSON receipt."""

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "submission"))
sys.path.insert(0, str(ROOT))
from hu_bot.v2_runtime import V2Runtime  # noqa: E402


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    from tests.test_v2_playable import fixtures

    runtime = V2Runtime()
    rows = []
    for raw in fixtures():
        action = runtime.decide(raw)
        rows.append({
            "hand_number": raw.hand_number,
            "street": raw.phase,
            "action": action.to_dict(),
            "valid": action.action in raw.valid_actions,
            "trace": {
                "node_digest": runtime.last_trace["node_key"]["digest"],
                "policy_reason": runtime.last_trace["policy"]["reason"],
                "ledger_status": runtime.last_trace["ledger"]["status"],
                "within_budget": runtime.last_trace["resource"]["within_budget"],
            },
        })
    receipt = {"schema_version": 1, "version": "2.0.0",
               "sample_count": len(rows), "legal_count": sum(row["valid"] for row in rows),
               "rows": rows}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(receipt, indent=2, sort_keys=True), encoding="utf-8")
    print(json.dumps({"sample_count": len(rows), "legal_count": receipt["legal_count"]}))


if __name__ == "__main__":
    main()

