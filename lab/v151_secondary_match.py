"""Secondary frozen v1.5.1 comparison under the v1.7 evaluator's assumed rules.

Run in a separate Python process with v1.5.1/submission first on PYTHONPATH.
"""
from dataclasses import replace
import json
from math import sqrt
from pathlib import Path
import random
import statistics

from hu_bot import __file__ as module_path, __version__
from hu_bot.config import Config
from hu_bot.engine import Engine
from hu_bot.poker import DECK
from lab.match import play_hand
from lab.profiles import Profile


def _one(seed, seat, rules, opponent):
    rng = random.Random(seed)
    bot = Engine(replace(Config(trace=False, retain_diagnostics=True,
                                learn_opponent=False, adapt_ranges=False,
                                adapt_responses=False, exploit_cap=0.0)))
    bot.reset(f"paired-{seed}-{seat}", rules["starting_bb"])
    villain = Profile("tight" if opponent == "normal" else opponent, seed+11)
    players = [villain, villain]
    players[seat] = bot
    stacks = [rules["starting_chips"]]*2
    hands, decisions, fallbacks = 0, 0, 0
    for hand_index in range(rules["max_hands_safety"]):
        if min(stacks) <= 0:
            break
        bb = (rules["starting_bb"] if rules["ramp_every_hands"] is None else
              rules["starting_bb"]*rules["ramp_factor"]**(
                  hand_index//rules["ramp_every_hands"]))
        deck = rng.sample(DECK, 52)
        _, traces, result = play_hand(players, deck, hand_index+1,
            button=hand_index % 2, bb=bb, ante=rules["ante"],
            starting_stacks=stacks)
        stacks = result["stacks"]
        hands += 1
        for trace in traces:
            if trace.get("match") == bot.match_id:
                decisions += 1
                fallbacks += bool(trace.get("fallback"))
    censored = min(stacks) > 0
    return {"hero_seat": seat, "win": None if censored else int(stacks[seat] > 0),
            "censored": censored, "hands": hands, "final_stacks": stacks,
            "decisions": decisions, "fallbacks": fallbacks}


def compare(candidate_receipt, output):
    if __version__ != "1.5.1" or "hu-profile-v1.5.1" not in str(module_path):
        raise ValueError(f"Expected frozen v1.5.1 import, got {__version__} {module_path}")
    output = Path(output)
    if output.exists():
        raise FileExistsError(output)
    candidate = json.loads(Path(candidate_receipt).read_text(encoding="utf-8"))
    if candidate["opponent"] not in {"normal", "call", "aggressive"}:
        raise ValueError("Unsupported opponent for secondary comparison")
    pairs = []
    for source_pair in candidate["pairs"]:
        seed = source_pair["seed"]
        old = [_one(seed, seat, candidate["rules"], candidate["opponent"])
               for seat in (0, 1)]
        new = source_pair["candidate"]
        def score(items):
            return sum(.5 if item["win"] is None else item["win"] for item in items)/2
        pairs.append({"seed": seed, "v151": old,
                      "v170_candidate": [{k: a[k] for k in ("hero_seat", "win", "censored",
                                      "hands", "final_stacks", "decisions", "fallbacks")}
                                         for a in new],
                      "paired_score_difference": score(new)-score(old)})
    differences = [p["paired_score_difference"] for p in pairs]
    mean = statistics.mean(differences)
    se = statistics.stdev(differences)/sqrt(len(differences)) if len(differences) > 1 else None
    result = {"baseline_version": __version__, "baseline_import": str(module_path),
              "candidate_receipt": str(candidate_receipt), "rules": candidate["rules"],
              "opponent": candidate["opponent"], "pairs": pairs,
              "paired_score_difference_mean": mean,
              "paired_normal_95_ci": None if se is None else [mean-1.96*se, mean+1.96*se],
              "interpretation": "Secondary cross-version comparison; no platform activation claim"}
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, indent=2)+"\n", encoding="utf-8")
    return {"pairs": len(pairs), "paired_score_difference_mean": mean}


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("candidate_receipt", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    print(json.dumps(compare(args.candidate_receipt, args.output)))
