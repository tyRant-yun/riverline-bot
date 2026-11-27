"""Small constrained-image performance smoke for the v2 playable baseline."""

import argparse
import json
from pathlib import Path
import time

from v2_fixture_data import fixtures
from hu_bot.v2_runtime import V2Runtime


def rss_mib():
    try:
        for line in open("/proc/self/status", encoding="ascii"):
            if line.startswith("VmHWM:"):
                return int(line.split()[1]) / 1024
    except (OSError, ValueError):
        return None
    return None


def percentile(values, q):
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, max(0, int(q * len(ordered) + .999) - 1))]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--timeout-ms", type=int, default=2000)
    parser.add_argument("--repeats", type=int, default=8)
    args = parser.parse_args()
    runtime = V2Runtime()
    runtime.timeout_ms = args.timeout_ms
    elapsed = []
    rows = []
    for raw in fixtures():
        for repeat in range(args.repeats):
            started = time.perf_counter()
            action = runtime.decide(raw)
            wall_ms = (time.perf_counter() - started) * 1000
            elapsed.append(wall_ms)
            rows.append({"hand_number": raw.hand_number, "repeat": repeat,
                         "action": action.to_dict(), "legal": action.action in raw.valid_actions,
                         "wall_ms": wall_ms,
                         "within_budget": runtime.last_trace["resource"]["within_budget"],
                         "fallback": runtime.last_trace["path"] != "v2"})
    receipt = {"schema_version": 1, "version": "2.0.0", "timeout_ms": args.timeout_ms,
               "fixture_count": len(fixtures()), "repeats": args.repeats,
               "sample_count": len(rows),
               "wall_ms": {"p50": percentile(elapsed, .50), "p95": percentile(elapsed, .95),
                           "p99": percentile(elapsed, .99), "max": max(elapsed)},
               "rss_peak_mib": rss_mib(),
               "illegal_count": sum(not row["legal"] for row in rows),
               "fallback_count": sum(row["fallback"] for row in rows),
               "rows": rows}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(receipt, indent=2), encoding="utf-8")
    print(json.dumps({"timeout_ms": args.timeout_ms, "sample_count": len(rows),
                      "p95_ms": receipt["wall_ms"]["p95"],
                      "illegal_count": receipt["illegal_count"]}))


if __name__ == "__main__":
    main()

