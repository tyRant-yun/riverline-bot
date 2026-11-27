"""Replay fixed states and compare actions with a frozen candidate root.

This is a development receipt, not a strength claim.  It records gate status,
same-state action differences, and the bounded runtime counters.  Profiles are
route labels only; no synthetic opponent EV is promoted to live policy.
"""

import argparse
import copy
import json
import os
from pathlib import Path
import subprocess
import sys


def load_root(root: Path):
    sys.path.insert(0, str(root / "submission"))
    sys.path.insert(0, str(root))
    from hu_bot.config import Config
    from hu_bot.engine import Engine
    from lab.scenarios import scenarios
    return Config, Engine, scenarios


def run_root(root: Path):
    Config, Engine, scenarios = load_root(root)
    source = scenarios()
    selected = [
        ("btn_2.25_open", source["btn_premium"]),
        ("bb_2.0_open", source["bb_vs_minraise"]),
        ("btn_3bet", source["btn_vs_3bet"]),
        ("river_overbet_defense", source["river_bluff_catcher"]),
        ("paired_river_value_sentinel", source["river_thin_value"]),
        ("heavy_history_700", copy.deepcopy(source["river_bluff_catcher"])),
    ]
    selected[-1][1].hand_number = 700
    selected[-1][1].action_history = list(selected[-1][1].action_history) + [
        {"seat": i % 2, "action": "check", "amount": 0, "phase": "river",
         "is_timeout": False} for i in range(720)
    ]
    engine = Engine(Config(seed=20261012, trace=False, retain_diagnostics=True,
                           max_samples=128, decision_budget_ms=450))
    engine.reset("v110-replay", 10, 900)
    rows = []
    for name, original in selected:
        state = copy.deepcopy(original)
        action = engine.decide(state)
        trace = engine.last_trace
        rows.append({
            "case": name,
            "action": action.to_dict(),
            "node_digest": trace.get("strength_slice", {}).get("node_digest"),
            "gate": trace.get("strength_slice", {}).get("strategy_gate", {}),
            "accounting_status": trace.get("accounting", {}).get("status",
                                                                  trace.get("accounting_status")),
            "elapsed_ms": trace.get("elapsed_ms"),
            "fallback": bool(trace.get("fallback")),
            "profile_scope": "fixed_state_route; no synthetic EV promotion",
        })
    return {"root": str(root), "version": engine.last_trace.get("version"),
            "seed": 20261012, "rows": rows,
            "counters": dict(engine.match_counters)}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--compare-root", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    candidate = run_root(args.root.resolve())
    payload = {"schema_version": 1, "candidate": candidate}
    if args.compare_root:
        baseline_output = args.compare_root.resolve() / "reports" / "v110-replay-baseline.json"
        command = [sys.executable, str(Path(__file__).resolve()),
                   "--root", str(args.compare_root.resolve()),
                   "--output", str(baseline_output)]
        env = os.environ.copy()
        subprocess.run(command, check=True, capture_output=True, text=True, env=env)
        baseline = json.loads(baseline_output.read_text(encoding="utf-8"))
        payload["baseline"] = baseline
        base_rows = {row["case"]: row for row in baseline["candidate"]["rows"]}
        payload["comparison"] = {
            "baseline_version": baseline["candidate"].get("version"),
            "same_state_cases": len(candidate["rows"]),
            "action_changes": sum(
                row["action"] != base_rows[row["case"]]["action"]
                for row in candidate["rows"] if row["case"] in base_rows
            ),
            "gate_enabled_count": sum(
                bool(row["gate"].get("enabled")) for row in candidate["rows"]
            ),
        }
    output = args.output or args.root.resolve() / "reports" / "v110-replay.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(json.dumps({"output": str(output),
                      "comparison": payload.get("comparison", {})}, indent=2))


if __name__ == "__main__":
    main()
