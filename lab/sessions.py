"""100BB duplicate SESSION evaluation with independent memories in each rotation.

Inference unit is an independent session-pair, not a hand. Fixed-stack cash hands,
not a platform tournament. Predeclare seeds/length; no significance-based stopping.
"""
import argparse
from concurrent.futures import ProcessPoolExecutor, as_completed
from copy import deepcopy
from dataclasses import replace
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
from hu_bot import __version__  # noqa: E402
from hu_bot.config import Config  # noqa: E402
from hu_bot.engine import Engine  # noqa: E402
from hu_bot.poker import DECK  # noqa: E402
from lab.match import play_hand  # noqa: E402
from lab.profiles import Profile, STYLES  # noqa: E402

MODES = ("frozen", "range", "full")


def configuration(mode):
    config = Config(trace=False)
    if mode == "frozen":
        return replace(config, learn_opponent=False, adapt_ranges=False,
                       adapt_responses=False, exploit_cap=0)
    if mode == "range":
        return replace(config, adapt_responses=False, exploit_cap=0)
    if mode == "full":
        return config
    raise ValueError(mode)


def opponent_factory(name):
    if name in STYLES:
        return lambda seed: Profile(name, seed)
    if name not in {"v1.0.0", "v1.1.0"}:
        raise ValueError(name)
    package = "frozen_"+name.replace(".", "_")
    if package not in sys.modules:
        path = ROOT/f".reference/hu-{name}/submission/hu_bot"
        if not path.exists():
            raise ValueError(f"Missing frozen source: {path}")
        spec = importlib.util.spec_from_file_location(package, path/"__init__.py",
                                                     submodule_search_locations=[str(path)])
        module = importlib.util.module_from_spec(spec)
        sys.modules[package] = module
        spec.loader.exec_module(module)
    engine = importlib.import_module(package+".engine").Engine
    config = importlib.import_module(package+".config").Config
    return lambda seed: engine(config(seed=seed, trace=False))


def interval(values):
    mean = statistics.mean(values)
    if len(values) < 2:
        return {"mean": mean, "normal_95_ci": None}
    margin = 1.96*statistics.stdev(values)/sqrt(len(values))
    return {"mean": mean, "normal_95_ci": [mean-margin, mean+margin]}


def evaluate_sessions(opponent="call", sessions=10, hands=150, seed=9211200,
                      modes=MODES, progress=None, session_offset=0):
    if sessions < 1 or hands < 1 or not modes or len(set(modes)) != len(modes):
        raise ValueError("Use positive session/hand counts and distinct modes")
    factory = opponent_factory(opponent)
    results = {mode: [] for mode in modes}
    diagnostics = {mode: {"decisions": 0, "fallbacks": 0, "opponent_fallbacks": 0,
                         "river_ev_decisions": 0, "response_adapted_decisions": 0,
                         "generic_exploit_decisions": 0, "max_decision_ms": 0.,
                         "end_max_opportunities": [], "end_response_opportunities": [],
                         "window_net_bb": [0., 0., 0.], "window_hands": [0, 0, 0]}
                   for mode in modes}
    start = perf_counter()
    for local_session in range(sessions):
        session = session_offset+local_session
        rng = random.Random(seed+session*10007)
        decks = [rng.sample(DECK, 52) for _ in range(hands)]
        for mode in modes:
            rotation_totals = []
            diag = diagnostics[mode]
            for hero_seat in (0, 1):
                bot = Engine(configuration(mode))
                bot.reset(f"session-{seed}-{session}-{hero_seat}")
                villain = factory(seed+session*10007+17)
                if hasattr(villain, "reset"):
                    villain.reset(f"opponent-{seed}-{session}-{hero_seat}")
                players = [villain, villain]
                players[hero_seat] = bot
                net_bb = 0.
                for index, deck in enumerate(decks):
                    net, traces, _ = play_hand(players, deck, index+1,
                                               button=index % 2, stack=1000, bb=10, ante=0)
                    gain = net[hero_seat]/10
                    net_bb += gain
                    window = 0 if index < 20 else 1 if index < 100 else 2
                    diag["window_net_bb"][window] += gain
                    diag["window_hands"][window] += 1
                    for trace in traces:
                        # Exception traces do not always contain a version; match identifies them.
                        if trace.get("match") != bot.match_id:
                            diag["opponent_fallbacks"] += bool(trace.get("fallback"))
                            continue
                        diag["decisions"] += 1
                        diag["fallbacks"] += bool(trace.get("fallback"))
                        diag["max_decision_ms"] = max(diag["max_decision_ms"], trace["elapsed_ms"])
                        rv = trace.get("river_ev", {})
                        diag["river_ev_decisions"] += bool(rv.get("complete"))
                        diag["response_adapted_decisions"] += any(
                            c.get("evidence") is not None for c in rv.get("candidates", []))
                        diag["generic_exploit_decisions"] += trace.get("exploit", {}).get("weight", 0) > 0
                rotation_totals.append(net_bb/hands*100)
                diag["end_max_opportunities"].append(max(
                    (s.opportunities for s in bot.opponent.stats.values()), default=0))
                diag["end_response_opportunities"].append(
                    bot.opponent.stats.get("response:river:fold").opportunities
                    if "response:river:fold" in bot.opponent.stats else 0)
            results[mode].append(statistics.mean(rotation_totals))
        if progress is not None:
            progress(local_session+1, sessions)
    contrasts = {}
    if "frozen" in modes:
        for mode in modes:
            if mode != "frozen":
                differences = [a-b for a, b in zip(results[mode], results["frozen"])]
                contrasts[mode+"_minus_frozen"] = interval(differences)
    for diag in diagnostics.values():
        diag["window_bb_per_100"] = [100*x/n if n else None for x, n in
                                      zip(diag["window_net_bb"], diag["window_hands"])]
    return {"version": __version__, "opponent": opponent, "seed": seed,
            "session_indices": list(range(session_offset, session_offset+sessions)),
            "sessions": sessions, "hands_per_rotation": hands,
            "hands_per_mode": sessions*hands*2, "total_hands": sessions*hands*2*len(modes),
            "conditions": {"depth_bb": 100, "ante": 0, "reset_stacks_each_hand": True},
            "unit": "independent duplicate session-pair; memory preserved within each rotation",
            "mode_config": {mode: configuration(mode).__dict__ for mode in modes},
            "bb_per_100": {mode: interval(values) for mode, values in results.items()},
            "paired_adaptation_gain_bb_per_100": contrasts, "session_pair_bb_per_100": results,
            "diagnostics": diagnostics, "elapsed_seconds": perf_counter()-start,
            "limitations": "Normal cluster CI is exploratory with few sessions; multiple comparisons unadjusted. Fixed 100BB cash hands and simple scripts, not tournament win rate or solved exploitability."}


def merge_sessions(parts):
    """Merge independent shards; never pool within-session hands for inference."""
    parts = sorted(parts, key=lambda part: part["session_indices"][0])
    result = deepcopy(parts[0])
    indices = [index for part in parts for index in part["session_indices"]]
    if len(set(indices)) != len(indices):
        raise ValueError("Overlapping session shards")
    for part in parts[1:]:
        for key in ("version", "opponent", "seed", "hands_per_rotation", "mode_config", "conditions"):
            if part[key] != result[key]:
                raise ValueError(f"Incompatible shards: {key}")
        for mode, diag in result["diagnostics"].items():
            other = part["diagnostics"][mode]
            for key in ("decisions", "fallbacks", "opponent_fallbacks", "river_ev_decisions",
                        "response_adapted_decisions", "generic_exploit_decisions"):
                diag[key] += other[key]
            diag["max_decision_ms"] = max(diag["max_decision_ms"], other["max_decision_ms"])
            for key in ("end_max_opportunities", "end_response_opportunities"):
                diag[key].extend(other[key])
            for key in ("window_net_bb", "window_hands"):
                diag[key] = [a+b for a, b in zip(diag[key], other[key])]
            result["session_pair_bb_per_100"][mode].extend(part["session_pair_bb_per_100"][mode])
    result["session_indices"] = indices
    result["sessions"] = len(indices)
    result["hands_per_mode"] = sum(p["hands_per_mode"] for p in parts)
    result["total_hands"] = sum(p["total_hands"] for p in parts)
    result["elapsed_seconds"] = sum(p["elapsed_seconds"] for p in parts)
    result["elapsed_note"] = "sum of worker times; wall time reported separately"
    values = result["session_pair_bb_per_100"]
    result["bb_per_100"] = {mode: interval(x) for mode, x in values.items()}
    result["paired_adaptation_gain_bb_per_100"] = {
        mode+"_minus_frozen": interval([a-b for a, b in zip(x, values["frozen"])])
        for mode, x in values.items() if mode != "frozen" and "frozen" in values}
    for diag in result["diagnostics"].values():
        diag["window_bb_per_100"] = [100*x/n if n else None for x, n in
                                    zip(diag["window_net_bb"], diag["window_hands"])]
    return result


def _session_worker(payload):
    return evaluate_sessions(**payload)


def evaluate_parallel(opponent, sessions, hands, seed, modes, workers, progress):
    start = perf_counter()
    with ProcessPoolExecutor(max_workers=min(workers, sessions)) as pool:
        futures = [pool.submit(_session_worker, {"opponent": opponent, "sessions": 1,
                    "hands": hands, "seed": seed, "modes": modes, "session_offset": index})
                   for index in range(sessions)]
        parts = []
        for future in as_completed(futures):
            parts.append(future.result())
            progress(len(parts), sessions)
    result = merge_sessions(parts)
    result["wall_seconds"] = perf_counter()-start
    result["workers"] = min(workers, sessions)
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--opponents", nargs="+", default=["call", "tight", "aggressive", "cbet_giveup"])
    parser.add_argument("--sessions", type=int, default=10)
    parser.add_argument("--hands", type=int, default=150)
    parser.add_argument("--seed", type=int, default=9211200)
    parser.add_argument("--modes", nargs="+", choices=MODES, default=list(MODES))
    parser.add_argument("--workers", type=int, default=1)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    if args.sessions < 2 or args.workers < 1:
        parser.error("Use at least two sessions and a positive worker count")
    targets = [args.output_dir/f"sessions-{name}.json" for name in args.opponents]
    if any(path.exists() for path in targets):
        raise SystemExit("Refusing to overwrite existing experiment results; choose a new output directory")
    args.output_dir.mkdir(parents=True, exist_ok=True)
    for opponent, path in zip(args.opponents, targets):
        def progress(done, total):
            print(f"{opponent}: session {done}/{total}", flush=True)
        if args.workers > 1:
            result = evaluate_parallel(opponent, args.sessions, args.hands, args.seed,
                                       tuple(args.modes), args.workers, progress)
        else:
            result = evaluate_sessions(opponent, args.sessions, args.hands, args.seed,
                                       tuple(args.modes), progress)
        path.write_text(json.dumps(result, indent=2), encoding="utf-8")
        print(json.dumps({"opponent": opponent, "results": result["bb_per_100"],
                          "gain": result["paired_adaptation_gain_bb_per_100"],
                          "elapsed_seconds": result["elapsed_seconds"]}), flush=True)


if __name__ == "__main__":
    main()
