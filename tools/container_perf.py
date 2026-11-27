"""Fixed-state performance probe executed inside the restricted image."""
import copy
import argparse
import hashlib
import json
import math
from pathlib import Path
import time

from chipzen import GameState
from chipzen.models import Card

from hu_bot.config import Config
from hu_bot.engine import Engine
from hu_bot import __version__


class Clock:
    def __init__(self):
        self.marks = {"start": time.perf_counter()}

    def mark(self, name):
        self.marks[name] = time.perf_counter()

    def durations(self):
        order = ["start", "normalized", "features", "ranges", "equity",
                 "policy", "legalized"]
        result = {}
        previous = "start"
        for name in order[1:]:
            if name in self.marks:
                result[name] = (self.marks[name] - self.marks[previous]) * 1000
                previous = name
        result["decision_total"] = (self.marks.get("legalized", time.perf_counter())
                                     - self.marks["start"]) * 1000
        return result


def rss_mib():
    try:
        for line in open("/proc/self/status", encoding="ascii"):
            if line.startswith("VmHWM:"):
                return int(line.split()[1]) / 1024
    except (OSError, ValueError):
        return None
    return None


def entry(seat, action, amount, phase):
    return {"seat": seat, "action": action, "amount": amount,
            "phase": phase, "is_timeout": False}


def state(name, phase, hole, board, history, pot, hero_stack, villain_stack,
          to_call, min_raise, max_raise, valid_actions, hand_number=1):
    return name, GameState(
        hand_number=hand_number, phase=phase,
        hole_cards=[Card.from_str(card) for card in hole],
        board=[Card.from_str(card) for card in board], pot=pot,
        your_stack=hero_stack, opponent_stacks=[villain_stack], your_seat=0,
        dealer_seat=0, to_call=to_call, min_raise=min_raise, max_raise=max_raise,
        valid_actions=valid_actions, action_history=history)


def cases():
    blinds = [entry(0, "post_small_blind", 5, "preflop"),
              entry(1, "post_big_blind", 10, "preflop")]
    srp = blinds + [entry(0, "raise", 25, "preflop"),
                    entry(1, "call", 15, "preflop")]
    prefix = [entry(0, "post_small_blind", 50, "preflop"),
              entry(1, "post_big_blind", 100, "preflop"),
              entry(0, "raise", 260, "preflop"),
              entry(1, "call", 160, "preflop"),
              entry(1, "check", 0, "flop"), entry(0, "raise", 436, "flop"),
              entry(1, "call", 436, "flop"), entry(1, "check", 0, "turn"),
              entry(0, "raise", 835, "turn"), entry(1, "raise", 3131, "turn"),
              entry(0, "call", 2296, "turn")]
    heavy = prefix + [entry(index % 2, "check", 0, "river") for index in range(720)]
    return [
        state("preflop_deep", "preflop", ("Ah", "Ad"), (), blinds,
              15, 995, 990, 5, 20, 1000, ["fold", "call", "raise"]),
        state("preflop_short", "preflop", ("8h", "8d"), (), blinds,
              15, 95, 90, 5, 20, 100, ["fold", "call", "raise"]),
        state("flop_wet", "flop", ("9s", "8s"), ("As", "7s", "6d"), srp,
              50, 975, 975, 0, 10, 975, ["check", "raise"]),
        state("turn_scare", "turn", ("Qh", "Jd"), ("Qs", "8s", "4d", "As"), srp,
              50, 975, 975, 0, 10, 975, ["check", "raise"]),
        state("river_high_loss", "river", ("Ah", "7d"),
              ("As", "8c", "4d", "2h", "9s"),
              srp + [entry(1, "raise", 100, "river")], 150, 975, 875, 100,
              200, 975, ["fold", "call", "raise"]),
        state("river_nuts", "river", ("As", "Ks"),
              ("Qs", "Js", "Ts", "2h", "3d"), srp,
              50, 975, 975, 0, 10, 975, ["check", "raise"]),
        state("river_heavy_history", "river", ("Ah", "7d"),
              ("As", "8c", "4d", "2h", "9s"), heavy,
              150, 975, 875, 100, 200, 975, ["fold", "call", "raise"], 700),
    ]


def percentile(values, q):
    ordered = sorted(values)
    return ordered[max(0, min(len(ordered) - 1, math.ceil(q * len(ordered)) - 1))]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path,
                        help="Write the full JSON receipt to this path")
    args = parser.parse_args()
    config = Config(seed=20260921, decision_budget_ms=450, max_samples=900,
                    trace=False, retain_diagnostics=True)
    engine = Engine(config)
    engine.reset("container-fixed-match", 10, 900)
    rows = []
    repeats = 16
    for case_name, original in cases():
        for repeat in range(repeats):
            raw = copy.deepcopy(original)
            clock = Clock()
            started = time.perf_counter()
            error = None
            try:
                action = engine.evaluate(raw, profiler=clock)
            except Exception as exc:  # retain a failure sample instead of hiding it
                action = None
                error = type(exc).__name__ + ": " + str(exc)
            elapsed = (time.perf_counter() - started) * 1000
            trace = engine.last_trace if isinstance(engine.last_trace, dict) else {}
            attribution = trace.get("attribution", {})
            reason_flags = attribution.get("reason_flags", {})
            range_summary = trace.get("villain_range", {})
            rows.append({
                "case": case_name, "repeat": repeat, "phase": raw.phase,
                "stages_ms": clock.durations(), "wall_total_ms": elapsed,
                "rss_peak_mib": rss_mib(), "over_budget": elapsed > 450,
                "budget_truncated": bool(reason_flags.get("deadline")) or
                trace.get("river_ev", {}).get("reason") == "deadline_atomic_discard",
                "incomplete_range": attribution.get("status") == "incomplete_range" or
                not bool(range_summary.get("complete", True)),
                "fallback": bool(trace.get("fallback")) or error is not None,
                "complete_result": bool(error is None and not trace.get("fallback") and
                                         attribution.get("status") not in
                                         {"deadline", "incomplete_range"}),
                "action": action.to_dict() if action is not None else None,
                "error": error,
            })
    stage_names = sorted({key for row in rows for key in row["stages_ms"]})
    summary = {"stages_ms": {stage: {
        "n": sum(stage in row["stages_ms"] for row in rows),
        "p50": percentile([row["stages_ms"][stage] for row in rows
                           if stage in row["stages_ms"]], .50),
        "p95": percentile([row["stages_ms"][stage] for row in rows
                           if stage in row["stages_ms"]], .95),
        "p99": percentile([row["stages_ms"][stage] for row in rows
                           if stage in row["stages_ms"]], .99),
    } for stage in stage_names}}
    totals = [row["wall_total_ms"] for row in rows]
    actions = [json.dumps(row["action"], sort_keys=True) for row in rows]
    actions_by_case = {}
    for case_name in sorted({row["case"] for row in rows}):
        actions_by_case[case_name] = len({json.dumps(row["action"], sort_keys=True)
                                          for row in rows if row["case"] == case_name})
    summary.update({
        "candidate_version": __version__, "sample_count": len(rows),
        "states": len(cases()), "repeats_per_state": repeats,
        "budget_ms": 450, "cpu_limit": 0.5, "memory_limit_mib": 256,
        "wall_total_ms": {"p50": percentile(totals, .50),
                          "p95": percentile(totals, .95),
                          "p99": percentile(totals, .99),
                          "max": max(totals)},
        "rss_peak_mib": max((row["rss_peak_mib"] or 0 for row in rows), default=None),
        "over_budget_count": sum(row["over_budget"] for row in rows),
        "budget_truncated_count": sum(row["budget_truncated"] for row in rows),
        "incomplete_range_count": sum(row["incomplete_range"] for row in rows),
        "fallback_count": sum(row["fallback"] for row in rows),
        "complete_result_count": sum(row["complete_result"] for row in rows),
        "same_state_unique_actions": len(set(actions)),
        "same_state_unique_actions_by_case": actions_by_case,
        "same_state_action_sha256": hashlib.sha256("\n".join(actions).encode()).hexdigest(),
    })
    receipt = {"schema_version": 1, "summary": summary, "samples": rows}
    rendered = json.dumps(receipt)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
        print(json.dumps({"schema_version": 1, "summary": summary}))
    else:
        print(rendered)


if __name__ == "__main__":
    main()
