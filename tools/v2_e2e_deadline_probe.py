"""Measure the real SDK endpoint from server turn send to action receive."""

from __future__ import annotations

import argparse
import asyncio
from collections import Counter
import json
from pathlib import Path
import statistics
import subprocess
import time
import uuid


def percentile(values, fraction):
    values = sorted(values)
    if not values:
        return None
    index = min(len(values) - 1, int((len(values) - 1) * fraction))
    return round(values[index], 3)


def make_turn(index, timeout_ms):
    return {
        "type": "turn_request",
        "match_id": "m_v220_e2e",
        "seq": index + 4,
        "server_ts": "local-contract",
        "seat": 0,
        "request_id": f"e2e-{index}",
        "timeout_ms": timeout_ms,
        "valid_actions": ["fold", "call", "raise"],
        "state": {
            "hand_number": index + 1,
            "phase": "preflop",
            "board": [],
            "your_hole_cards": ["Ah", "Kd"],
            "pot": 15,
            "your_stack": 995,
            "opponent_stacks": [990],
            "to_call": 5,
            "min_raise": 20,
            "max_raise": 995,
            "action_history": [
                {"seat": 0, "action": "post_small_blind", "amount": 5,
                 "phase": "preflop", "is_timeout": False},
                {"seat": 1, "action": "post_big_blind", "amount": 10,
                 "phase": "preflop", "is_timeout": False},
            ],
        },
    }


async def server(samples, timeout_ms):
    from websockets.asyncio.server import serve

    done = asyncio.Event()
    receipt = {"samples": samples, "timeout_ms": timeout_ms, "latencies_ms": [],
               "legal": 0, "illegal": 0, "actions": Counter(), "action_indices": {},
               "bad_responses": []}

    async def handler(ws):
        auth = json.loads(await asyncio.wait_for(ws.recv(), 10))
        if auth.get("type") != "authenticate":
            raise AssertionError(auth)
        await ws.send(json.dumps({
            "type": "hello", "match_id": "m_v220_e2e", "seq": 1,
            "supported_versions": ["1.0"], "selected_version": "1.0",
            "game_type": "nlhe_6max", "capabilities": [],
        }))
        client_hello = json.loads(await asyncio.wait_for(ws.recv(), 10))
        if client_hello.get("type") != "hello":
            raise AssertionError(client_hello)
        await ws.send(json.dumps({
            "type": "match_start", "match_id": "m_v220_e2e", "seq": 2,
            "seats": [{"seat": 0, "participant_id": "p0", "is_self": True},
                      {"seat": 1, "participant_id": "p1", "is_self": False}],
            "game_config": {"variant": "nlhe", "starting_stack": 1000,
                            "small_blind": 5, "big_blind": 10, "ante": 0},
            "turn_timeout_ms": timeout_ms,
        }))
        await ws.send(json.dumps({
            "type": "round_start", "match_id": "m_v220_e2e", "seq": 3,
            "round_id": "r_v210_e2e", "round_number": 1,
            "state": {"hand_number": 1, "dealer_seat": 0,
                      "your_hole_cards": ["Ah", "Kd"], "stacks": [995, 990]},
        }))
        for index in range(samples):
            turn = make_turn(index, timeout_ms)
            started = time.perf_counter()
            await ws.send(json.dumps(turn))
            response = json.loads(await asyncio.wait_for(ws.recv(), max(1, timeout_ms / 1000 * 4)))
            elapsed = (time.perf_counter() - started) * 1000
            receipt["latencies_ms"].append(elapsed)
            legal = response.get("type") == "turn_action" and response.get("request_id") == turn["request_id"]
            if legal and response.get("action") == "raise":
                amount = response.get("params", {}).get("amount")
                legal = 20 <= amount <= 995
            receipt["legal"] += int(legal)
            receipt["illegal"] += int(not legal)
            receipt["actions"][response.get("action", "missing")] += 1
            receipt["action_indices"].setdefault(response.get("action", "missing"), []).append(index)
            if not legal and len(receipt["bad_responses"]) < 3:
                receipt["bad_responses"].append(response)
        await ws.send(json.dumps({"type": "match_end", "match_id": "m_v220_e2e",
                                  "seq": samples + 4, "reason": "complete", "results": []}))
        await asyncio.sleep(.5)
        done.set()

    async with serve(handler, "0.0.0.0", 8765):
        print("READY", flush=True)
        await asyncio.wait_for(done.wait(), max(60, samples * 2))
    latencies = receipt.pop("latencies_ms")
    receipt["actions"] = dict(receipt["actions"])
    receipt["p50_ms"] = percentile(latencies, .50)
    receipt["p95_ms"] = percentile(latencies, .95)
    receipt["p99_ms"] = percentile(latencies, .99)
    receipt["max_ms"] = round(max(latencies), 3) if latencies else None
    receipt["mean_ms"] = round(statistics.mean(latencies), 3) if latencies else None
    print(json.dumps(receipt, sort_keys=True), flush=True)


def host(tag, samples, timeout_ms):
    root = Path(__file__).resolve().parents[1]
    suffix = uuid.uuid4().hex[:8]
    network = f"hu-v220-e2e-{suffix}"
    name = f"hu-v220-e2e-server-{suffix}"

    def run(*args, **kwargs):
        return subprocess.run(args, check=True, capture_output=True, text=True, **kwargs)

    run("docker", "network", "create", "--internal", network)
    try:
        run("docker", "run", "-d", "--name", name, "--network", network,
            "--mount", f"type=bind,source={root / 'tools'},target=/tools,readonly",
            "--entrypoint", "python", tag, "/tools/v2_e2e_deadline_probe.py",
            "--server", "--samples", str(samples), "--timeout-ms", str(timeout_ms))
        try:
            result = run("docker", "run", "--rm", "--network", network, "--cpus=.5",
                         "--memory=256m", "--read-only", "--cap-drop=ALL",
                         "--user=10001:10001", "--security-opt=no-new-privileges",
                         "--tmpfs", "/tmp:rw,size=10m",
                         "-e", "CHIPZEN_WS_URL=ws://%s:8765/ws/match/m_v220_e2e/p0" % name,
                         "-e", "CHIPZEN_TOKEN=local-smoke-only", tag)
        except subprocess.CalledProcessError as exc:
            server_logs = subprocess.run(["docker", "logs", name],
                                         capture_output=True, text=True)
            print(server_logs.stdout + server_logs.stderr + exc.stdout + exc.stderr)
            raise
        logs = ""
        for _ in range(50):
            logs = run("docker", "logs", name).stdout
            if '"p50_ms"' in logs:
                break
            time.sleep(.1)
        output = logs + result.stdout + result.stderr
        report_dir = root / "reports" / "v2.2.0"
        report_dir.mkdir(exist_ok=True)
        (report_dir / f"e2e-deadline-{samples}-{timeout_ms}ms.log").write_text(output, encoding="utf-8")
        print(output)
    finally:
        subprocess.run(["docker", "rm", "-f", name], capture_output=True)
        subprocess.run(["docker", "network", "rm", network], capture_output=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--server", action="store_true")
    parser.add_argument("--tag", default="hu-bot:2.2.0")
    parser.add_argument("--samples", type=int, default=1000)
    parser.add_argument("--timeout-ms", type=int, default=2000)
    args = parser.parse_args()
    if args.server:
        asyncio.run(server(args.samples, args.timeout_ms))
    else:
        host(args.tag, args.samples, args.timeout_ms)
