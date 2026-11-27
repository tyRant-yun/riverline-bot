"""Evaluate the unchanged production entry at legally reached river states."""

from pathlib import Path
import json

from hu_bot.config import Config
from hu_bot.engine import Engine
from hu_bot.poker import DECK
from lab.double_pair_river import BOARD, Hero, Villain
from lab.match import play_hand


class ProbeHero(Hero):
    def __init__(self, river, facing):
        super().__init__(river, facing)
        self.engine = Engine(Config(trace=False, retain_diagnostics=True))
        self.runtime = []

    def decide(self, state):
        if state.phase == "river":
            action = self.engine.evaluate(state)
            trace = self.engine.last_trace
            self.runtime.append({"pot": state.pot, "to_call": state.to_call,
                                 "stack": state.your_stack, "action": action.to_dict(),
                                 "fallback": trace["fallback"],
                                 "elapsed_ms": trace["elapsed_ms"]})
        return super().decide(state)


def run(output):
    output = Path(output)
    if output.exists():
        raise FileExistsError(output)
    rows = []
    for hole in (("Qs", "5s"), ("Ks", "Ts"), ("9s", "7s"), ("6s", "2s")):
        for villain in (("Ad", "2h"), ("Kd", "Kh"), ("8s", "7s")):
            if set(hole) & set(villain):
                continue
            deck = list(villain) + list(hole) + list(BOARD)
            deck += [c for c in DECK if c not in deck]
            hero_player = ProbeHero("medium", "call")
            villain_player = Villain(BOARD, hole, villain, "normal", "medium", 191401)
            _, _, result = play_hand([villain_player, hero_player], deck,
                                     button=0, bb=100, starting_stacks=[10000, 9034])
            assert sum(result["stacks"]) == 19034
            assert hero_player.runtime[0]["pot"] == 7654
            rows.append({"hero": hole, "villain": villain,
                         "production_decisions_at_forced_path": hero_player.runtime,
                         "forced_river_actions": [e for e in result["action_history"]
                                                  if e["phase"] == "river"]})
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps({"version": "1.9.1", "rows": rows,
        "note": "Frozen v1.9.0 production Engine is evaluated at each reachable river state; the scripted player executes counterfactual arms to complete the hand. It is not an online intervention."},
        separators=(",", ":")) + "\n", encoding="utf-8")
    return {"states": sum(len(row["production_decisions_at_forced_path"])
                          for row in rows), "hands": len(rows)}


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    print(json.dumps(run(args.output)))
