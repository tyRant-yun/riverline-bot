"""Small paired seat-swap match smoke for the v1.10 candidate.

The result is explicitly exploratory: fixed 100BB cash-hand rules and simple
scripted opponents are not the platform tournament and do not validate
strength.  It exists to catch illegal actions, cashflow drift, and fallbacks.
"""

import argparse
import json
from pathlib import Path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--pairs", type=int, default=8)
    args = parser.parse_args()
    from lab.match import benchmark

    results = []
    for name, opponent, seed in (("normal", "tight", 11001),
                                 ("reverse_value", "aggressive", 11002)):
        result = benchmark(pairs=max(2, args.pairs), opponent=opponent, seed=seed)
        results.append({"profile": name, "opponent": opponent, **result})
    payload = {
        "schema_version": 1,
        "rules": "fixed_100bb_no_ante_cash_hand_smoke",
        "not_platform_tournament": True,
        "results": results,
        "interpretation": "engineering smoke only; no strength or Swiss claim",
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(json.dumps({"output": str(args.output),
                      "profiles": [row["profile"] for row in results]}, indent=2))


if __name__ == "__main__":
    main()
