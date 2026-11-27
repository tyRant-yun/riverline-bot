"""Bounded, fingerprinted conditional HU evaluation harness.

Each unit is one complete paired session (both seats and all declared modes).
Held-out seeds are predeclared and never used for tuning or stopping decisions.
"""
from __future__ import annotations

import argparse
from dataclasses import replace
import hashlib
import importlib
import importlib.util
import json
from pathlib import Path
import random
import sys
import statistics
from time import perf_counter

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "submission"))
from hu_bot.config import Config  # noqa: E402
from hu_bot.engine import Engine  # noqa: E402
from hu_bot.poker import DECK  # noqa: E402
from lab.match import play_hand  # noqa: E402
from lab.profile_family import ARCHETYPES, ProfileFamily, parameter_manifest  # noqa: E402
from lab.checkpoints import source_fingerprints  # noqa: E402

MODES = ("baseline", "candidate_off", "raise_guard", "value_only", "both")
TRAIN_SEEDS = (140001, 140002, 140003, 140004)
HOLDOUT_SEEDS = (140201, 140202)


def _sha(value) -> str:
    data = json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    return hashlib.sha256(data).hexdigest()


def _tree_sha(path: Path) -> str:
    files = {p.relative_to(path).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
             for p in sorted(path.rglob("*")) if p.is_file() and "__pycache__" not in p.parts
             and p.suffix != ".pyc"}
    return _sha(files)


def _t95(n: int) -> float:
    # Two-sided 95% Student-t critical values for the small cluster counts used here.
    table = {1: 12.706, 2: 4.303, 3: 3.182, 4: 2.776, 5: 2.571, 6: 2.447,
             7: 2.365, 8: 2.306, 9: 2.262, 10: 2.228, 11: 2.201, 12: 2.179,
             13: 2.160, 14: 2.145, 15: 2.131, 16: 2.120, 17: 2.110,
             18: 2.101, 19: 2.093, 20: 2.086, 21: 2.080, 22: 2.074,
             23: 2.069, 24: 2.064, 25: 2.060, 26: 2.056, 27: 2.052,
             28: 2.048, 29: 2.045, 30: 2.042}
    return table.get(max(1, min(30, n - 1)), 1.96)


def _cluster_interval(values):
    values = [float(value) for value in values]
    n = len(values)
    mean = statistics.mean(values) if values else None
    if n < 2:
        return {"units": n, "mean": mean, "t95_ci": None}
    se = statistics.stdev(values) / n**0.5
    margin = _t95(n) * se
    return {"units": n, "mean": mean, "t95_ci": [mean - margin, mean + margin]}


def protocol(archetypes, hands=20):
    if hands < 1 or hands > 150:
        raise ValueError("hands must be in [1, 150]")
    names = tuple(archetypes)
    if not names or any(name not in ARCHETYPES for name in names):
        raise ValueError("unknown or empty archetype list")
    units = []
    for split, seeds in (("train", TRAIN_SEEDS), ("holdout", HOLDOUT_SEEDS)):
        for name in names:
            for seed in seeds:
                units.append({"unit_id": f"{split}-{name}-{seed}", "split": split,
                              "opponent": name, "seed": seed, "hands": hands,
                              "modes": list(MODES), "seats": [0, 1]})
    return {"version": "v1.4.0", "modes": list(MODES),
            "mode_config": {mode: {"decision_budget_ms": 900,
                "unpriced_raise_guard": mode in {"raise_guard", "both"},
                "conditional_value_enabled": mode in {"value_only", "both"}}
                for mode in MODES}, "units": units, "rules": {"stack_bb": 100,
            "ante": 0, "paired_decks_and_seats": True, "independent_memory": True},
            "train_seeds": list(TRAIN_SEEDS), "holdout_seeds": list(HOLDOUT_SEEDS),
            "archetype_parameters": parameter_manifest()}


def _config(mode):
    if mode == "baseline":
        package = _frozen_v13_package()
        return package.Engine(package.Config(trace=False, decision_budget_ms=900))
    config = Config(trace=False, decision_budget_ms=900,
                    unpriced_raise_guard=False, conditional_value_enabled=False)
    if mode == "candidate_off":
        return Engine(config)
    if mode == "raise_guard":
        config = replace(config, unpriced_raise_guard=True)
    elif mode == "value_only":
        config = replace(config, conditional_value_enabled=True)
    elif mode == "both":
        config = replace(config, unpriced_raise_guard=True, conditional_value_enabled=True)
    else:
        raise ValueError(mode)
    return Engine(config)


_FROZEN = None


def _frozen_v13_package():
    global _FROZEN
    if _FROZEN is not None:
        return _FROZEN
    path = ROOT / "references" / "hu-overbet-v1.3.0" / "submission" / "hu_bot"
    if not (path / "__init__.py").exists():
        raise ValueError(f"missing frozen v1.3 source: {path}")
    spec = importlib.util.spec_from_file_location("frozen_hu_v13", path / "__init__.py",
        submodule_search_locations=[str(path)])
    package = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = package
    spec.loader.exec_module(package)
    package.Engine = importlib.import_module(spec.name + ".engine").Engine
    package.Config = importlib.import_module(spec.name + ".config").Config
    _FROZEN = package
    return package


def _run_unit(unit):
    start = perf_counter()
    results = {mode: {"net_bb": 0.0, "hands": 0, "decisions": 0, "interventions": 0,
                      "fallbacks": 0, "max_ms": 0.0, "river_first_action_opportunities": 0,
                      "river_overbet_opportunities": 0, "river_overbet_successes": 0,
                      "river_selected_reveals": 0, "raise_guard_applied": 0,
                      "conditional_value_applied": 0, "incomplete_range_decisions": 0,
                      "river_deadline_atomic_discards": 0, "v14_deadline_discards": 0}
               for mode in MODES}
    action_sequences = {mode: [] for mode in MODES}
    for mode in MODES:
        decks_rng = random.Random(unit["seed"])
        decks = [decks_rng.sample(DECK, 52) for _ in range(unit["hands"])]
        for hero_seat in (0, 1):
            bot = _config(mode)
            bot.reset(f"v14-{unit['unit_id']}-{hero_seat}")
            villain = ProfileFamily(unit["opponent"], unit["seed"] + 17)
            villain.reset(f"opp-{unit['unit_id']}-{hero_seat}")
            players = [villain, villain]
            players[hero_seat] = bot
            for index, deck in enumerate(decks, 1):
                net, traces, hand_result = play_hand(players, deck, index, button=(index - 1) % 2,
                                            stack=1000, bb=10, ante=0)
                rec = results[mode]
                rec["net_bb"] += net[hero_seat] / 10
                rec["hands"] += 1
                action_sequences[mode].append((net[hero_seat], tuple(
                    (entry["action"], entry.get("amount", 0))
                    for entry in hand_result["action_history"]
                    if entry.get("seat") == hero_seat and not entry["action"].startswith("post_"))))
                for trace in traces:
                    if trace.get("match") != bot.match_id:
                        continue
                    rec["decisions"] += 1
                    rec["fallbacks"] += int(bool(trace.get("fallback")))
                    rec["max_ms"] = max(rec["max_ms"], float(trace.get("elapsed_ms", 0)))
                    rec["raise_guard_applied"] += int(bool(
                        trace.get("unpriced_raise_guard", {}).get("applied")))
                    rec["conditional_value_applied"] += int(bool(
                        trace.get("conditional_value", {}).get("applied")))
                    flags = trace.get("attribution", {}).get("reason_flags", {})
                    rec["incomplete_range_decisions"] += int(flags.get("incomplete_range") is True)
                    rec["river_deadline_atomic_discards"] += int(
                        trace.get("river_ev", {}).get("reason") == "deadline_atomic_discard")
                    rec["v14_deadline_discards"] += int(
                        trace.get("conditional_value", {}).get("reason") == "deadline_atomic_discard"
                        or trace.get("unpriced_raise_guard", {}).get("reason")
                        in {"deadline", "deadline_atomic_discard"})
            # Opportunity denominators and selected showdown reveals are distinct
            # counters; neither is a bluff-rate estimate.
            for key, stat in bot.opponent.stats.items():
                if key.startswith("river:first:"):
                    if key.endswith(":bet"):
                        results[mode]["river_first_action_opportunities"] += stat.opportunities
                    elif key.endswith(":overbet"):
                        results[mode]["river_overbet_opportunities"] += stat.opportunities
                        results[mode]["river_overbet_successes"] += stat.success
                    elif key.endswith(":revealed_weak"):
                        results[mode]["river_selected_reveals"] += stat.opportunities
    for rec in results.values():
        rec["interventions"] = rec["raise_guard_applied"] + rec["conditional_value_applied"]
        rec["bb_per_100"] = rec["net_bb"] / rec["hands"] * 100
    if action_sequences["baseline"] != action_sequences["candidate_off"]:
        raise ValueError("baseline/candidate_off hero action parity mismatch")
    unclear = [(mode, results[mode]["incomplete_range_decisions"],
                results[mode]["river_deadline_atomic_discards"],
                results[mode]["v14_deadline_discards"]) for mode in MODES
               if results[mode]["incomplete_range_decisions"] or
               results[mode]["river_deadline_atomic_discards"] or
               results[mode]["v14_deadline_discards"]]
    if unclear:
        raise ValueError(f"attribution incomplete or deadline: {unclear}")
    results["contrasts"] = {mode: results[mode]["bb_per_100"] - results["candidate_off"]["bb_per_100"]
                            for mode in MODES if mode not in {"baseline", "candidate_off"}}
    results["frozen_baseline_contrasts"] = {
        "candidate_off": results["candidate_off"]["bb_per_100"] - results["baseline"]["bb_per_100"]}
    return {"unit": unit["unit_id"], "split": unit["split"], "opponent": unit["opponent"],
            "seed": unit["seed"], "results": results, "elapsed_seconds": perf_counter() - start}


def run(output_dir, archetypes=None, hands=20, smoke=False, split="all"):
    if split not in {"train", "holdout", "all"}:
        raise ValueError("split must be train, holdout, or all")
    names = tuple(archetypes or ARCHETYPES)
    spec = protocol(names, hands if not smoke else min(hands, 2))
    spec["smoke"] = bool(smoke)
    selected_split = "train" if smoke else split
    if selected_split != "all":
        spec["units"] = [unit for unit in spec["units"] if unit["split"] == selected_split]
    spec["selected_split"] = selected_split
    frozen_path = ROOT.parent / "hu-overbet-v1.3.0" / "submission"
    spec["source_fingerprint"] = {"candidate": source_fingerprints(ROOT),
                                   "frozen_v13": _tree_sha(frozen_path)}
    spec["opponent_fingerprint"] = _sha(parameter_manifest())
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=False)
    (output / "protocol.json").write_text(json.dumps(spec, indent=2), encoding="utf-8")
    completed = []
    for unit in spec["units"]:
        current_fingerprint = {"candidate": source_fingerprints(ROOT),
                               "frozen_v13": _tree_sha(frozen_path)}
        if current_fingerprint != spec["source_fingerprint"]:
            raise RuntimeError("source fingerprint drift before unit " + unit["unit_id"])
        try:
            result = _run_unit(unit)
        except BaseException as exc:
            failure = {"unit": unit["unit_id"], "error": {"type": type(exc).__name__,
                       "message": str(exc)}}
            temp = output / f".{unit['unit_id']}.failure.tmp"
            temp.write_text(json.dumps(failure, indent=2), encoding="utf-8")
            temp.replace(output / f"{unit['unit_id']}.failure.json")
            continue
        current_fingerprint = {"candidate": source_fingerprints(ROOT),
                               "frozen_v13": _tree_sha(frozen_path)}
        if current_fingerprint != spec["source_fingerprint"]:
            raise RuntimeError("source fingerprint drift after unit " + unit["unit_id"])
        temp = output / f".{unit['unit_id']}.tmp"
        temp.write_text(json.dumps(result, indent=2), encoding="utf-8")
        temp.replace(output / f"{unit['unit_id']}.json")
        completed.append(result)
    paired = {"candidate_off_minus_baseline": _cluster_interval([
        item["results"]["frozen_baseline_contrasts"]["candidate_off"] for item in completed])}
    metrics = {}
    for mode in MODES:
        if mode in {"baseline", "candidate_off"}:
            continue
        values = [item["results"]["contrasts"][mode] for item in completed]
        paired[mode] = _cluster_interval(values)
    for mode in MODES:
        metrics[mode] = {}
        for key in ("interventions", "fallbacks", "max_ms", "river_first_action_opportunities"):
            metrics[mode][key] = _cluster_interval([item["results"][mode][key]
                                                     for item in completed])
    summary = {"protocol": _sha(spec), "selected_split": selected_split,
               "completed": len(completed), "expected": len(spec["units"]),
               "complete": len(completed) == len(spec["units"]), "units": completed,
               "paired_cluster_summary": paired, "unit_metric_summary": metrics,
               "uncertainty": "Student-t 95% intervals over independent session units; exploratory and unadjusted."}
    (output / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    return {"output": str(output), "units": len(completed), "hands": sum(
        item["results"]["baseline"]["hands"] for item in completed)}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--archetypes", nargs="+", default=list(ARCHETYPES))
    parser.add_argument("--hands", type=int, default=20)
    parser.add_argument("--smoke", action="store_true")
    parser.add_argument("--split", choices=("train", "holdout", "all"), default="all")
    args = parser.parse_args()
    print(json.dumps(run(args.output, args.archetypes, args.hands, args.smoke, args.split), indent=2))


if __name__ == "__main__":
    main()
