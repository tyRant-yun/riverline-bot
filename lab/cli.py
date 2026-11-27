"""python -m lab.cli scenarios|spot|analyze|replay|benchmark ..."""
import argparse
from dataclasses import replace
import json
from pathlib import Path
import statistics
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/"submission"))
from chipzen import GameState
from chipzen.models import Card
from hu_bot.config import Config
from hu_bot.engine import Engine
from hu_bot.memory import Posterior
from lab.scenarios import scenarios


def raw_state(data):
    data = dict(data)
    for key in ("hole_cards", "board"):
        data[key] = [Card(**c) if isinstance(c, dict) else Card.from_str(c) for c in data.get(key, [])]
    return GameState(**data)


def read_traces(path):
    for line in Path(path).read_text(encoding="utf-8-sig").splitlines():
        if "TRACE " in line:
            line = line.split("TRACE ", 1)[1]
        if line.startswith("{"):
            yield json.loads(line)


def analyze(traces):
    traces = list(traces)
    flags = []
    strong_river, missed_value, marginal_large, fallback = 0, 0, 0, 0
    for t in traces:
        action = t["action"]["action"]
        f = t.get("features", {})
        if t.get("fallback"):
            fallback += 1
        if t.get("street") == "river" and t.get("position") == "BTN" and not t.get("to_call") and (
            t.get("equity", {}).get("nuts") and not f.get("board_only") or
            f.get("made") in {"STRONG", "VERY_STRONG"} and t.get("equity", {}).get("equity", 0) > .75):
            strong_river += 1
            if action == "check":
                missed_value += 1
                flags.append({"hand": t["hand"], "type": "MissedValue", "node": t.get("node")})
        if f.get("made") in {"MEDIUM", "WEAK_SHOWDOWN"} and action == "raise" and t.get("spr", 0) > 3:
            amount = t["action"].get("amount", 0)
            if amount >= .5*t.get("hero_stack", float("inf")):
                marginal_large += 1
                flags.append({"hand": t["hand"], "type": "MarginalOverplay"})
    latencies = sorted(t.get("elapsed_ms", 0) for t in traces)
    return {"decisions": len(traces), "fallbacks": fallback,
            "strong_river_opportunities": strong_river,
            "strong_river_checkbacks": missed_value,
            "strong_river_checkback_rate": missed_value/strong_river if strong_river else None,
            "marginal_large_raises": marginal_large,
            "latency_ms": {"p50": statistics.median(latencies) if latencies else 0,
                           "p95": latencies[int((len(latencies)-1)*.95)] if latencies else 0,
                           "max": max(latencies, default=0)}, "flags": flags,
            "limitations": "Heuristic decision flags; not solver EV or full-hand profitability."}


def main():
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("scenarios")
    p.add_argument("--output", default="reports/scenarios.jsonl")
    p = sub.add_parser("spot")
    p.add_argument("path")
    p = sub.add_parser("analyze")
    p.add_argument("path")
    p = sub.add_parser("replay")
    p.add_argument("path")
    p.add_argument("--index", type=int, default=0)
    p = sub.add_parser("benchmark")
    p.add_argument("--pairs", type=int, default=100)
    p.add_argument("--opponent", choices=["call", "tight", "aggressive", "random"], default="tight")
    p.add_argument("--seed", type=int, default=42)
    p.add_argument("--output", default="reports/benchmark.json")
    args = parser.parse_args()
    config = Config(trace=False)
    if args.command == "scenarios":
        traces = []
        for name, state in scenarios().items():
            engine = Engine(config)
            engine.evaluate(state)
            traces.append({"scenario": name, **engine.last_trace})
        path = Path(args.output)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("\n".join(json.dumps(t) for t in traces)+"\n", encoding="utf-8")
        print(json.dumps(analyze(traces), indent=2))
    elif args.command == "spot":
        engine = Engine(config)
        engine.evaluate(raw_state(json.loads(Path(args.path).read_text(encoding="utf-8-sig"))))
        print(json.dumps(engine.last_trace, indent=2))
    elif args.command == "analyze":
        print(json.dumps(analyze(read_traces(args.path)), indent=2))
    elif args.command == "replay":
        trace = list(read_traces(args.path))[args.index]
        from hu_bot import __version__
        if trace.get("version") != __version__:
            raise SystemExit(f"Use the matching source release to replay version {trace.get('version')}; current runtime is {__version__}")
        replay = trace["replay"]
        if "config" in replay:
            config = Config(**replay["config"])
        config = replace(config, seed=replay["seed"], decision_budget_ms=5000,
                         max_samples=trace["equity"]["samples"] or replay["max_samples"])
        engine = Engine(config)
        engine.reset(replay["match_id"], replay["big_blind"], timeout_ms=10000)
        for k, v in replay["opponent"].items():
            engine.opponent.stats[k] = Posterior(v["prior"], v.get("prior_weight", 20), v["success"], v["opportunities"])
        engine.evaluate(raw_state(replay["state"]), replay_trace=trace)
        print(json.dumps({"original": trace["action"], "replayed": engine.last_trace["action"],
            "same_sample": trace["random"] == engine.last_trace["random"],
            "same_policy": trace["policy"] == engine.last_trace["policy"],
            "same_equity": trace["equity"] == engine.last_trace["equity"],
            "note": "Replayed with recorded completed range updates and sample count."}, indent=2))
    elif args.command == "benchmark":
        from lab.match import benchmark
        result = benchmark(args.pairs, args.opponent, args.seed)
        path = Path(args.output)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(result, indent=2), encoding="utf-8")
        print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
