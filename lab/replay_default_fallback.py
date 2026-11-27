"""Replay a candidate-only legal remap from an independent match receipt."""
from dataclasses import asdict
import json
from pathlib import Path

from hu_bot.engine import Engine
from lab.hu100_matches import MatchEvaluator, MatchRules


def replay(output):
    output = Path(output)
    if output.exists():
        raise FileExistsError(output)
    events = []
    original = Engine.evaluate

    def captured(self, raw, replay_trace=None):
        action = original(self, raw, replay_trace)
        if self.last_trace.get("fallback"):
            events.append({"hand": raw.hand_number, "street": raw.phase,
                "fallback": self.last_trace["fallback"],
                "preflop_range": self.last_trace.get("preflop_range"),
                "policy": self.last_trace.get("policy"),
                "action": action.to_dict(), "raw": asdict(raw)})
        return action

    Engine.evaluate = captured
    try:
        match = MatchEvaluator(MatchRules(starting_chips=600), (10052,),
                               "exact_open_overfolder")._one(10052, 0, True)
    finally:
        Engine.evaluate = original
    result = {"version": "1.8.3", "seed": 10052, "hero_seat": 0,
              "opponent": "exact_open_overfolder", "starting_chips": 600,
              "recorded_fallbacks": match["fallbacks"], "events": events}
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, indent=2)+"\n", encoding="utf-8")
    return {"fallbacks": len(events), "hands": [e["hand"] for e in events]}


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    print(json.dumps(replay(args.output)))
