"""Compare frozen v1.8.4 and v1.9.0 on coherent and conflicting inputs."""
import argparse
from dataclasses import replace
import json
import os
from pathlib import Path
import random
import subprocess
import sys

from chipzen import GameState
from chipzen.models import Card

ROOT = Path(__file__).resolve().parents[1]
WORK = ROOT.parent
SEED = 983491


def event(seat, action, amount):
    return {"seat": seat, "action": action, "amount": amount,
            "phase": "preflop", "is_timeout": False}


def spot(context, depth, price, cards, hand):
    bb, sb = 100, 50
    history = [event(0, "post_small_blind", sb), event(1, "post_big_blind", bb)]
    bets = [sb, bb]
    if context == "BB_VS_LIMP":
        history.append(event(0, "call", sb))
        bets[0] = bb
    elif context == "BB_VS_OPEN":
        bets[0] = round(price*bb)
        history.append(event(0, "raise", bets[0]))
    elif context != "BTN_FIRST_IN":
        raise ValueError(context)
    seat = 0 if context == "BTN_FIRST_IN" else 1
    owed = max(0, bets[1-seat]-bets[seat])
    chips = depth*bb
    return GameState(hand_number=hand, phase="preflop",
        hole_cards=[Card.from_str(card) for card in cards], board=[],
        pot=sum(bets), your_stack=chips-bets[seat],
        opponent_stacks=[chips-bets[1-seat]], your_seat=seat, dealer_seat=0,
        to_call=owed, min_raise=max(bets)+bb, max_raise=chips,
        valid_actions=["fold", "call", "raise"] if owed else ["check", "raise"],
        action_history=history)


def snapshot():
    import hu_bot
    from hu_bot.config import Config
    from hu_bot.core import normalize
    from hu_bot.engine import Engine
    from hu_bot.poker import DECK
    from hu_bot.preflop import preflop_policy

    if not str(Path(hu_bot.__file__).resolve()).startswith(str(WORK.resolve())):
        raise RuntimeError(f"Unexpected import: {hu_bot.__file__}")
    rng = random.Random(SEED)
    config = Config(seed=SEED, trace=False, retain_diagnostics=True,
                    learn_opponent=False, adapt_ranges=False, adapt_responses=False)

    def probe(raw):
        state = normalize(raw, 100)
        policy = preflop_policy(state, use_range_spec=True).normalized()
        old_policy = preflop_policy(state, use_range_spec=False).normalized()
        bot, old = Engine(config), Engine(replace(config, range_repair_enabled=False))
        bot.reset("history-gate-new-seed", 100)
        old.reset("history-gate-new-seed", 100)
        action, old_action = bot.decide(raw), old.decide(raw)
        assert not bot.last_trace["fallback"] and not old.last_trace["fallback"]
        return {"policy": policy.weights, "old_policy": old_policy.weights,
                "action": action.to_dict(), "old_action": old_action.to_dict(),
                "applied": bot.last_trace["preflop_range"]["applied"]}

    coherent = []
    windows = [("BTN_FIRST_IN", 60, 0), ("BB_VS_LIMP", 100, 1),
               ("BB_VS_OPEN", 60, 2), ("BB_VS_OPEN", 60, 2.25)]
    for context, depth, price in windows:
        for index in range(96):
            cards = tuple(str(card) for card in rng.sample(DECK, 2))
            raw = spot(context, depth, price, cards, 1000+len(coherent))
            coherent.append({"context": context, "cards": cards, **probe(raw)})
    stale = []
    for index in range(96):
        cards = tuple(str(card) for card in rng.sample(DECK, 2))
        raw = spot("BB_VS_OPEN", 60, 2, cards, 2000+index)
        raw = replace(raw, pot=1100, to_call=900, min_raise=1900,
                      opponent_stacks=[5000])
        stale.append({"cards": cards, **probe(raw)})
    pot_conflict = []
    for index in range(96):
        cards = tuple(str(card) for card in rng.sample(DECK, 2))
        raw = spot("BB_VS_OPEN", 60, 2.25, cards, 3000+index)
        raw = replace(raw, pot=raw.pot+100)
        pot_conflict.append({"cards": cards, **probe(raw)})
    return {"version": hu_bot.__version__, "import": str(Path(hu_bot.__file__).resolve()),
            "seed": SEED, "coherent": coherent, "stale": stale,
            "pot_conflict": pot_conflict}


def compare():
    versions = [("1.8.4", WORK/"hu-history-v1.8.4"),
                ("1.9.0", ROOT)]
    outputs = []
    for version, candidate in versions:
        env = dict(os.environ)
        env["PYTHONPATH"] = os.pathsep.join((str(candidate/"submission"), str(candidate)))
        result = subprocess.run([sys.executable, str(Path(__file__).resolve()), "--snapshot"],
                                cwd=candidate, env=env, capture_output=True,
                                text=True, check=True)
        output = json.loads(result.stdout)
        assert output["version"] == version
        assert Path(output["import"]).resolve() == (candidate/"submission/hu_bot/__init__.py").resolve()
        outputs.append(output)
    old, new = outputs
    coherent_equal = all(
        all(a[key] == b[key] for key in ("context", "cards", "policy", "action", "applied"))
        for a, b in zip(old["coherent"], new["coherent"], strict=True))
    stale_old_changes = sum(row["action"] != row["old_action"] for row in old["stale"])
    stale_new_changes = sum(row["action"] != row["old_action"] for row in new["stale"])
    stale_new_safe = all(row["policy"] == row["old_policy"] and
                         row["action"] == row["old_action"] and not row["applied"]
                         for row in new["stale"])
    assert coherent_equal and stale_old_changes == 0
    assert stale_new_safe and stale_new_changes == 0
    conflict_old_changes = sum(row["action"] != row["old_action"]
                               for row in old["pot_conflict"])
    conflict_new_changes = sum(row["action"] != row["old_action"]
                               for row in new["pot_conflict"])
    assert conflict_new_changes == 0
    assert all(row["policy"] == row["old_policy"] and not row["applied"]
               for row in new["pot_conflict"])
    result = {"seed": SEED, "coherent_rows": len(new["coherent"]),
              "coherent_policy_action_equal": coherent_equal,
              "coherent_candidate_action_changes_vs_old": sum(
                  row["action"] != row["old_action"] for row in new["coherent"]),
              "stale_rows": len(new["stale"]),
              "v184_stale_action_changes_vs_old": stale_old_changes,
              "v190_stale_action_changes_vs_old": stale_new_changes,
              "v190_stale_old_policy_and_action_equal": stale_new_safe,
              "pot_conflict_rows": len(new["pot_conflict"]),
              "v184_pot_conflict_action_changes_vs_old": conflict_old_changes,
              "v190_pot_conflict_action_changes_vs_old": conflict_new_changes,
              "imports": {row["version"]: row["import"] for row in outputs}}
    destination = ROOT/"reports/history-gate-comparison.json"
    destination.write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--snapshot", action="store_true")
    args = parser.parse_args()
    if args.snapshot:
        print(json.dumps(snapshot()))
    else:
        compare()
