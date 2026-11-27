"""Exercise the actual release ENTRYPOINT over real WebSockets on an isolated Docker network."""
import argparse
import asyncio
import json
from pathlib import Path
import subprocess
import uuid


async def server():
    from websockets.asyncio.server import serve
    from chipzen.conformance import _full_match_script
    done = asyncio.Event()

    async def handler(ws):
        messages = _full_match_script()
        auth = json.loads(await asyncio.wait_for(ws.recv(), 10))
        assert auth["type"] == "authenticate" and auth["token"] == "local-smoke-only"
        await ws.send(json.dumps(messages[0]))
        hello = json.loads(await asyncio.wait_for(ws.recv(), 10))
        assert hello["type"] == "hello" and hello["client_name"] in {"hu-bot-v1", "hu-bot-v2"}
        for message in messages[1:]:
            await ws.send(json.dumps(message))
            if message["type"] == "turn_request":
                action = json.loads(await asyncio.wait_for(ws.recv(), 2))
                assert action["type"] == "turn_action"
                assert action["request_id"] == message["request_id"]
                assert action["action"] in message["valid_actions"]
                if action["action"] == "raise":
                    assert message["state"]["min_raise"] <= action["params"]["amount"] <= message["state"]["max_raise"]
        print(json.dumps({"entrypoint": "passed", "authentication": "passed",
                          "request_id": "passed", "action": action}), flush=True)
        done.set()

    async with serve(handler, "0.0.0.0", 8765):
        print("READY", flush=True)
        await asyncio.wait_for(done.wait(), 25)


def host(tag):
    root = Path(__file__).resolve().parents[1]
    suffix = uuid.uuid4().hex[:8]
    network, name = f"hu-v1-smoke-{suffix}", f"hu-v1-server-{suffix}"

    def run(*args, **kwargs):
        return subprocess.run(args, check=True, capture_output=True, text=True, **kwargs)

    run("docker", "network", "create", "--internal", network)
    try:
        run("docker", "run", "-d", "--name", name, "--network", network,
            "--mount", f"type=bind,source={root / 'tools'},target=/tools,readonly",
            "--entrypoint", "python", tag, "/tools/wire_smoke.py", "--server")
        result = run("docker", "run", "--rm", "--network", network, "--cpus=.5",
            "--memory=256m", "--read-only", "--cap-drop=ALL", "--user=10001:10001",
            "--security-opt=no-new-privileges", "--tmpfs", "/tmp:rw,size=10m",
            "-e", f"CHIPZEN_WS_URL=ws://{name}:8765/ws/match/m_conformance_test/p0",
            "-e", "CHIPZEN_TOKEN=local-smoke-only", tag, timeout=30)
        logs = run("docker", "logs", name).stdout
        if '"entrypoint": "passed"' not in logs:
            raise RuntimeError(logs)
        directory = root/"reports"/f"v{tag.rsplit(':', 1)[1]}"
        directory.mkdir(exist_ok=True)
        (directory/"wire-smoke.log").write_text(logs+result.stdout+result.stderr, encoding="utf-8")
        print(logs)
    finally:
        subprocess.run(["docker", "rm", "-f", name], capture_output=True)
        subprocess.run(["docker", "network", "rm", network], capture_output=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--server", action="store_true")
    parser.add_argument("--tag", default="hu-bot:1.1.0")
    args = parser.parse_args()
    if args.server:
        asyncio.run(server())
    else:
        host(args.tag)
