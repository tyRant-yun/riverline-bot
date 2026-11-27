"""Reproducible decision-stage benchmark for the v1.9.2 candidate.

This is a measurement harness only.  It does not alter strategy switches or
write into the submitted package.  RSS is the Windows process peak working set;
the enforced target is documented as 0.5 CPU / 256 MiB / 450 ms.
"""
import argparse
import copy
import ctypes
import json
import math
import os
import statistics
import sys
import time
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
BENCH_ROOT = Path(os.environ.get("HU_BENCH_ROOT", str(ROOT))).resolve()
sys.path.insert(0, str(BENCH_ROOT / "submission"))
sys.path.insert(0, str(BENCH_ROOT))

from lab.scenarios import scenarios  # noqa: E402
from hu_bot.config import Config  # noqa: E402
from hu_bot.engine import Engine  # noqa: E402
import inspect  # noqa: E402


class StageClock:
    def __init__(self):
        self.marks = {"start": time.perf_counter()}

    def mark(self, name):
        self.marks[name] = time.perf_counter()

    def durations(self):
        order = ["start", "normalized", "features", "ranges", "equity",
                 "policy", "legalized"]
        result = {}
        prior = "start"
        for name in order[1:]:
            if name in self.marks:
                result[name] = round((self.marks[name] - self.marks[prior]) * 1000, 6)
                prior = name
        result["decision_total"] = round(
            (self.marks.get("legalized", time.perf_counter()) - self.marks["start"]) * 1000, 6)
        return result


class _Counters(ctypes.Structure):
    _fields_ = [("cb", ctypes.c_ulong), ("page_fault_count", ctypes.c_ulong),
                ("peak_working_set", ctypes.c_size_t), ("working_set", ctypes.c_size_t),
                ("quota_peak_paged_pool", ctypes.c_size_t),
                ("quota_paged_pool", ctypes.c_size_t),
                ("quota_peak_nonpaged_pool", ctypes.c_size_t),
                ("quota_nonpaged_pool", ctypes.c_size_t),
                ("pagefile", ctypes.c_size_t), ("peak_pagefile", ctypes.c_size_t),
                ("private_usage", ctypes.c_size_t)]


def peak_rss_bytes():
    counters = _Counters()
    counters.cb = ctypes.sizeof(counters)
    psapi = ctypes.WinDLL("psapi")
    kernel32 = ctypes.WinDLL("kernel32")
    psapi.GetProcessMemoryInfo.argtypes = [ctypes.c_void_p, ctypes.POINTER(_Counters),
                                           ctypes.c_ulong]
    psapi.GetProcessMemoryInfo.restype = ctypes.c_int
    ok = psapi.GetProcessMemoryInfo(kernel32.GetCurrentProcess(),
                                    ctypes.byref(counters), counters.cb)
    return int(counters.peak_working_set) if ok else None


def percentile(values, q):
    if not values:
        return None
    ordered = sorted(float(v) for v in values)
    return round(ordered[max(0, min(len(ordered) - 1, math.ceil(q * len(ordered)) - 1))], 6)


def cases():
    base = scenarios()
    selected = [
        ("preflop_deep", base["btn_premium"]),
        ("preflop_short", base["ten_bb_jam"]),
        ("preflop_3bet", base["btn_vs_3bet"]),
        ("flop_wet", base["flop_combo_draw"]),
        ("turn_scare", base["turn_scare"]),
        ("river_high_loss", base["river_bluff_catcher"]),
        ("river_nuts", base["river_nuts"]),
    ]
    heavy = copy.deepcopy(base["river_bluff_catcher"])
    heavy.hand_number = 700
    heavy.action_history = list(heavy.action_history) + [
        {"seat": index % 2, "action": "check", "amount": 0,
         "phase": "river", "is_timeout": False} for index in range(720)
    ]
    selected.append(("river_heavy_history", heavy))
    return selected


def run(repeats):
    config = Config(seed=20260921, decision_budget_ms=450, max_samples=900,
                    trace=False, retain_diagnostics=True, learn_opponent=False)
    engine = Engine(config)
    engine.reset("perf-fixed-match", 10, 900)
    supports_profiler = "profiler" in inspect.signature(engine.evaluate).parameters
    rows = []
    for case_name, original in cases():
        for repeat in range(repeats):
            state = copy.deepcopy(original)
            state.hand_number = max(1, int(original.hand_number))
            probe = StageClock()
            started = time.perf_counter()
            try:
                if supports_profiler:
                    action = engine.evaluate(state, profiler=probe)
                else:
                    action = engine.evaluate(state)
                    probe.mark("legalized")
                error = None
            except Exception as exc:  # keep a raw failure sample in the receipt
                action = None
                error = type(exc).__name__ + ": " + str(exc)
            elapsed = (time.perf_counter() - started) * 1000
            trace = engine.last_trace if isinstance(engine.last_trace, dict) else {}
            durations = probe.durations()
            durations["wall_total"] = round(elapsed, 6)
            rows.append({
                "case": case_name,
                "repeat": repeat,
                "phase": state.phase,
                "stack_bb": round(min(state.your_stack, state.opponent_stacks[0]) / 10, 3),
                "hand_number": state.hand_number,
                "stages_ms": durations,
                "rss_peak_bytes": peak_rss_bytes(),
                "budget_ms": 450,
                "over_budget": elapsed > 450,
                "fallback": bool(trace.get("fallback")) or error is not None,
                "complete_result": bool(error is None and not trace.get("fallback") and
                                         trace.get("attribution", {}).get("status")
                                         not in {"deadline", "incomplete_range"}),
                "action": action.to_dict() if action is not None else None,
                "error": error,
                "accounting_status": trace.get("accounting_status",
                                                trace.get("accounting", {}).get("status")),
            })
    return config, rows


def summarize(rows):
    stage_names = sorted({stage for row in rows for stage in row["stages_ms"]})
    summary = {}
    for stage in stage_names:
        values = [row["stages_ms"][stage] for row in rows if stage in row["stages_ms"]]
        summary[stage] = {"n": len(values), "p50": percentile(values, .50),
                          "p95": percentile(values, .95), "p99": percentile(values, .99),
                          "max": round(max(values), 6) if values else None}
    elapsed = [row["stages_ms"]["wall_total"] for row in rows]
    return {
        "stages_ms": summary,
        "rss_peak_bytes": max((row["rss_peak_bytes"] or 0 for row in rows), default=None),
        "budget_ms": 450,
        "cpu_limit": 0.5,
        "memory_limit_bytes": 256 * 1024 * 1024,
        "decision_count": len(rows),
        "over_budget_count": sum(row["over_budget"] for row in rows),
        "over_budget_rate": round(sum(row["over_budget"] for row in rows) / max(1, len(rows)), 6),
        "complete_result_count": sum(row["complete_result"] for row in rows),
        "complete_result_rate": round(sum(row["complete_result"] for row in rows) /
                                       max(1, len(rows)), 6),
        "fallback_count": sum(row["fallback"] for row in rows),
        "rss_peak_mib": round(max((row["rss_peak_bytes"] or 0 for row in rows), default=0) /
                               1024 / 1024, 6),
        "elapsed_mean_ms": round(statistics.mean(elapsed), 6),
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--output", type=Path, default=ROOT / "reports" / "performance-baseline.json")
    args = parser.parse_args()
    config, rows = run(max(1, args.repeats))
    payload = {
        "schema_version": 1,
        "candidate": os.environ.get("HU_BENCH_LABEL", "hu-rules-v1.9.2"),
        "source": ("frozen hu-river-v1.9.1 plus accounting adapter"
                   if "rules-v1.9.2" in os.environ.get("HU_BENCH_LABEL", "hu-rules-v1.9.2")
                   else "frozen source total-time comparison"),
        "seed": config.seed,
        "max_samples": config.max_samples,
        "cases": [row["case"] for row in rows[::max(1, args.repeats)]],
        "summary": summarize(rows),
        "samples": rows,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(json.dumps(payload["summary"], indent=2))
    print(f"wrote {args.output}")


if __name__ == "__main__":
    main()
