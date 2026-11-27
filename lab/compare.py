"""Version-to-version holdout duplicate tests; loads frozen release under a separate namespace."""
import argparse
from contextlib import contextmanager
import hashlib
import importlib
import importlib.util
import json
from math import sqrt
from pathlib import Path
import random
import statistics
import sys
from time import perf_counter

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/"submission"))
from hu_bot.engine import Engine  # noqa: E402
from hu_bot.config import Config  # noqa: E402
from hu_bot.poker import DECK  # noqa: E402
from lab.match import play_hand  # noqa: E402


def baseline_classes():
    # Restored from the original verified source archive; no text rewriting/import collisions.
    path = ROOT/".reference/hu-v1.0.0/submission/hu_bot"
    if not path.exists():
        raise SystemExit("Restore the frozen v1.0 source ZIP under .reference/hu-v1.0.0 first")
    spec = importlib.util.spec_from_file_location("hu_v10", path/"__init__.py",
                                                 submodule_search_locations=[str(path)])
    module = importlib.util.module_from_spec(spec)
    sys.modules["hu_v10"] = module
    spec.loader.exec_module(module)
    return importlib.import_module("hu_v10.engine").Engine, importlib.import_module("hu_v10.config").Config


@contextmanager
def component_override(component):
    """Diagnostic-only component swaps, always restored, never used in uploaded runtime."""
    baseline_classes()
    runtime = importlib.import_module("hu_bot.engine")
    original_policy, original_range = runtime.preflop_policy, runtime.posterior
    try:
        if component == "range-only":
            runtime.preflop_policy = importlib.import_module("hu_v10.strategy").preflop_policy
        elif component == "chart-only":
            legacy_range = importlib.import_module("hu_v10.ranges").posterior

            def adapter(state, seat, deadline, max_updates=None, opponent_model=None):
                return legacy_range(state, seat, deadline, max_updates)

            runtime.posterior = adapter
        elif component != "full":
            raise ValueError(component)
        yield
    finally:
        runtime.preflop_policy, runtime.posterior = original_policy, original_range


def compare(depth=100, pairs=50, seed=8675309):
    if pairs < 2:
        raise ValueError("Need at least two duplicate pairs")
    LegacyEngine, LegacyConfig = baseline_classes()
    rng = random.Random(seed)
    # Fresh engines for each hand, including both rotations; pair outcomes have no memory carryover.
    # This measures cold-start play, not adaptation. A session-level evaluation is still needed.
    outcomes, timings = [], {"v1.0": [], "v1.1": []}
    failures = {"v1.0": 0, "v1.1": 0}
    exceptions = {"v1.0": 0, "v1.1": 0}
    remap_examples = []
    decisions = {"v1.0": 0, "v1.1": 0}
    start = perf_counter()
    for i in range(pairs):
        deck = rng.sample(DECK, 52)
        results = []
        for seat in (0, 1):
            new = Engine(Config(trace=False))
            old = LegacyEngine(LegacyConfig(trace=False))
            new.reset(f"holdout-{seed}-{i}")
            old.reset(f"holdout-{seed}-{i}")
            players = [old, old]
            players[seat] = new
            net, traces, _ = play_hand(players, deck, hand_number=i+1, button=i % 2,
                                       stack=round(depth*10), bb=10)
            results.append(net[seat]/10)
            for t in traces:
                version = "v1.1" if t["version"] == "1.1.0" else "v1.0"
                decisions[version] += 1
                failures[version] += bool(t.get("fallback"))
                if t.get("fallback"):
                    exceptions[version] += "policy" not in t
                    if len(remap_examples) < 8:
                        remap_examples.append({k: t.get(k) for k in
                            ("version", "hand", "street", "node", "selected", "action", "fallback", "policy")})
                timings[version].append(t["elapsed_ms"])
        outcomes.append(sum(results)/2)
    mean, se = statistics.mean(outcomes), statistics.stdev(outcomes)/sqrt(pairs)
    return {"depth_bb": depth, "pairs": pairs, "hands": 2*pairs, "seed": seed,
            "v1.1_bb_per_100_vs_v1.0": 100*mean,
            "normal_95_ci": [100*(mean-1.96*se), 100*(mean+1.96*se)],
            "pair_outcomes_bb": outcomes, "decisions": decisions, "fallbacks": failures,
            "exceptions": exceptions, "remap_examples": remap_examples,
            "max_decision_ms": {k: max(v, default=0) for k, v in timings.items()},
            "elapsed_seconds": round(perf_counter()-start, 2),
            "method": "same deck and button, seat rotation; fresh match memory each pair; no tuning on holdout seed"}


def main():
    from hu_bot import __version__
    if __version__ != "1.1.0":
        raise SystemExit("This archived v1.1 comparison driver requires v1.1 source. Use lab.sessions for current releases.")
    parser = argparse.ArgumentParser()
    parser.add_argument("--pairs", type=int, default=60)
    parser.add_argument("--seed", type=int, default=8675309)
    parser.add_argument("--depths", type=float, nargs="+", default=[8, 12, 20, 40, 100])
    parser.add_argument("--component", choices=["full", "chart-only", "range-only"], default="full")
    args = parser.parse_args()
    for depth in args.depths:
        with component_override(args.component):
            result = compare(depth, args.pairs, args.seed+int(depth))
        result["component"] = args.component
        prefix = "compare" if args.component == "full" else f"ablation-{args.component}"
        path = ROOT/f"reports/v1.1.0/{prefix}-{depth:g}bb.json"
        path.write_text(json.dumps(result, indent=2), encoding="utf-8")
        print(json.dumps({k: v for k, v in result.items() if k != "pair_outcomes_bb"}), flush=True)
    archive = ROOT/"dist/hu-bot-v1.0.0-source.zip"
    print("baseline_source_sha256="+hashlib.sha256(archive.read_bytes()).hexdigest(), flush=True)


if __name__ == "__main__":
    main()
