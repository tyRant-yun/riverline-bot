"""Run the same compact developer fixture through v2 or a frozen legacy engine."""

import argparse
import json
from pathlib import Path

from chipzen import GameState
from chipzen.models import Card


def load_fixtures():
    from v2_fixture_data import fixtures
    return fixtures()


def raw_copy(raw):
    return GameState(hand_number=raw.hand_number, phase=raw.phase,
                     hole_cards=[Card.from_str(str(card)) for card in raw.hole_cards],
                     board=[Card.from_str(str(card)) for card in raw.board], pot=raw.pot,
                     your_stack=raw.your_stack, opponent_stacks=[raw.opponent_stacks[0]],
                     your_seat=raw.your_seat, dealer_seat=raw.dealer_seat,
                     to_call=raw.to_call, min_raise=raw.min_raise, max_raise=raw.max_raise,
                     valid_actions=list(raw.valid_actions),
                     action_history=[dict(event) for event in raw.action_history])


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=("v2", "legacy"), required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.mode == "v2":
        from hu_bot.v2_runtime import V2Runtime
        runner = V2Runtime()
    else:
        from hu_bot.config import Config
        from hu_bot.engine import Engine
        runner = Engine(Config(trace=False, retain_diagnostics=True, max_samples=64))
        runner.reset("v2-behavior-compare", 10, 2000)
    rows = []
    for fixture in load_fixtures():
        raw = raw_copy(fixture)
        if args.mode == "v2":
            runner.big_blind = max(1, int(next((e["amount"] for e in raw.action_history
                                                if e["action"] == "post_big_blind"), 10)))
            action = runner.decide(raw)
            reason = runner.last_trace.get("policy", {}).get("reason")
        else:
            action = runner.decide(raw)
            reason = runner.last_trace.get("policy", {}).get("reason")
        rows.append({"fixture": fixture.hand_number, "action": action.to_dict(),
                     "legal": action.action in raw.valid_actions, "reason": reason})
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps({"mode": args.mode, "rows": rows}, indent=2),
                           encoding="utf-8")
    print(json.dumps({"mode": args.mode, "count": len(rows)}))


if __name__ == "__main__":
    main()

