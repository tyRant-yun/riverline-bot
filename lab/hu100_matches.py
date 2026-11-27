"""Assumed-rules continuous-stack HU match pairs, separate from platform claims."""
from dataclasses import asdict, dataclass, replace
import json
from math import sqrt
from pathlib import Path
import random
import statistics

from chipzen import Action

from hu_bot import __version__
from hu_bot.config import Config
from hu_bot.engine import Engine
from hu_bot.poker import DECK
from lab.match import play_hand
from lab.profiles import Profile


@dataclass(frozen=True)
class MatchRules:
    starting_chips: int = 1000
    starting_bb: int = 10
    ante: int = 0
    ramp_every_hands: int | None = 25
    ramp_factor: int = 2
    max_hands_safety: int = 500
    source: str = "assumed_for_research"
    evidence_uri: str | None = None

    def __post_init__(self):
        if self.source not in {"assumed_for_research", "platform_verified"}:
            raise ValueError("Match rules need an explicit provenance")
        if self.source == "platform_verified" and not self.evidence_uri:
            raise ValueError("Verified match rules need a source receipt")
        if min(self.starting_chips, self.starting_bb) <= 0 or self.ante < 0:
            raise ValueError("Invalid match chips or ante")

    def blind(self, hand_index):
        if self.ramp_every_hands is None:
            return self.starting_bb
        return self.starting_bb*self.ramp_factor**(hand_index//self.ramp_every_hands)


class ExactOpenOverfolder(Profile):
    def __init__(self, seed):
        super().__init__("tight", seed)

    def decide(self, state):
        pf = [e for e in state.action_history if e.get("phase") == "preflop"
              and not e["action"].startswith("post_")]
        if (state.phase == "preflop" and state.your_seat != state.dealer_seat
                and len(pf) == 1 and pf[0]["seat"] == state.dealer_seat
                and pf[0]["action"] == "raise"
                and 2.1 <= pf[0]["amount"]/max(1, state.action_history[1]["amount"]) <= 2.4):
            if self.rng.random() < .80:
                return Action.fold()
            return Action.call()
        return super().decide(state)


class Exact25Opener(Profile):
    def decide(self, state):
        pf = [e for e in state.action_history if e.get("phase") == "preflop"
              and not e["action"].startswith("post_")]
        if (state.phase == "preflop" and state.your_seat == state.dealer_seat
                and not pf and "raise" in state.valid_actions):
            bb = int(state.action_history[1]["amount"])
            target = round(2.5*bb)
            return Action.raise_to(max(state.min_raise, min(state.max_raise, target)))
        return super().decide(state)


class MatchEvaluator:
    def __init__(self, rules, seeds, opponent="normal", comparison="range",
                 within_match_learning=False):
        if opponent not in {"normal", "call", "aggressive", "exact_open_overfolder",
                            "exact_25_tight", "exact_25_call", "exact_25_aggressive"}:
            raise ValueError(opponent)
        if comparison not in {"range", "range_extension", "overfold_research"}:
            raise ValueError(comparison)
        self.rules, self.seeds, self.opponent = rules, tuple(seeds), opponent
        self.comparison = comparison
        self.within_match_learning = within_match_learning

    def _one(self, seed, hero_seat, enabled):
        rng = random.Random(seed)
        bot = Engine(replace(Config(trace=False, retain_diagnostics=True,
                                    learn_opponent=self.within_match_learning,
                                    adapt_ranges=self.within_match_learning,
                                    adapt_responses=self.within_match_learning,
                                    exploit_cap=.15 if self.within_match_learning else 0.0),
                             range_repair_enabled=(enabled if self.comparison == "range" else True),
                             range_extension_enabled=(enabled if self.comparison == "range_extension"
                                                      else False),
                             preflop_overfold_enabled=(enabled if self.comparison == "overfold_research"
                                                       else False)))
        bot.reset(f"paired-{seed}-{hero_seat}", self.rules.starting_bb)
        if self.opponent == "exact_open_overfolder":
            villain = ExactOpenOverfolder(seed+11)
        elif self.opponent.startswith("exact_25_"):
            villain = Exact25Opener(self.opponent.removeprefix("exact_25_"), seed+11)
        else:
            villain = Profile("tight" if self.opponent == "normal" else self.opponent,
                              seed+11)
        players = [villain, villain]
        players[hero_seat] = bot
        stacks = [self.rules.starting_chips]*2
        trajectory, decisions, fallbacks, changed, max_ms = [], 0, 0, 0, 0.0
        exploit_reached, exploit_interventions, extension_interventions = 0, 0, 0
        fallback_details = []
        for hand_index in range(self.rules.max_hands_safety):
            if min(stacks) <= 0:
                break
            bb = self.rules.blind(hand_index)
            deck = rng.sample(DECK, 52)
            _, traces, result = play_hand(players, deck, hand_index+1,
                                          button=hand_index % 2, bb=bb,
                                          ante=self.rules.ante,
                                          starting_stacks=stacks)
            stacks = result["stacks"]
            trajectory.append({"hand": hand_index+1, "bb": bb,
                               "hero_chips": stacks[hero_seat]})
            for trace in traces:
                if trace.get("match") != bot.match_id:
                    continue
                decisions += 1
                fallbacks += bool(trace.get("fallback"))
                if trace.get("fallback") and len(fallback_details) < 5:
                    fallback_details.append({"hand": trace.get("hand"),
                        "street": trace.get("street"), "reason": trace["fallback"]})
                changed += trace.get("preflop_range", {}).get("sampled_action_changed", False)
                extension_interventions += bool(
                    trace.get("preflop_range", {}).get("research_window") and
                    trace.get("preflop_range", {}).get("sampled_action_changed"))
                exploit_reached += trace.get("node") == "PREFLOP_BTN_FIRST_IN"
                exploit_interventions += bool(trace.get("preflop_overfold", {}).get("applied"))
                max_ms = max(max_ms, trace.get("elapsed_ms", 0))
        censored = min(stacks) > 0
        return {"hero_seat": hero_seat, "win": None if censored else int(stacks[hero_seat] > 0),
                "censored": censored, "hands": len(trajectory), "final_stacks": stacks,
                "trajectory": trajectory, "decisions": decisions, "fallbacks": fallbacks,
                "policy_interventions": changed, "max_decision_ms": max_ms,
                "exploit_reached": exploit_reached,
                "exploit_interventions": exploit_interventions,
                "extension_interventions": extension_interventions,
                "fallback_details": fallback_details,
                "opponent_opportunities": bot.opponent.preflop.summary()}

    def evaluate(self):
        pairs = []
        for seed in self.seeds:
            arms = {}
            for enabled, name in ((False, "baseline"), (True, "candidate")):
                arms[name] = [self._one(seed, seat, enabled) for seat in (0, 1)]
            def score(name):
                return sum(.5 if item["win"] is None else item["win"]
                           for item in arms[name])/2
            pairs.append({"seed": seed, **arms, "paired_score_difference":
                          score("candidate")-score("baseline")})
        differences = [p["paired_score_difference"] for p in pairs]
        mean = statistics.mean(differences)
        margin = (1.96*statistics.stdev(differences)/sqrt(len(differences))
                  if len(differences) > 1 else None)
        censored = sum(item["censored"] for p in pairs
                       for name in ("baseline", "candidate") for item in p[name])
        return {"version": __version__, "rules": asdict(self.rules),
                "comparison": self.comparison,
                "within_match_learning": self.within_match_learning,
                "opponent": self.opponent, "seeds": self.seeds,
                "independent_unit": "paired seed, both seats, distinct match-local memories",
                "match_count": 4*len(pairs), "censored_matches": censored,
                "paired_score_difference_mean": mean,
                "paired_normal_95_ci": None if margin is None else [mean-margin, mean+margin],
                "win_probability_interpretable": censored == 0,
                "pairs": pairs,
                "limitations": "Assumed schedule, simple scripted opponent, safety cap. "
                               "No platform rules or strength confirmation."}


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("output", type=Path)
    parser.add_argument("--opponent", choices=("normal", "call", "aggressive",
                                               "exact_open_overfolder"),
                        default="normal")
    parser.add_argument("--seeds", type=int, nargs="+", default=[9401, 9402])
    parser.add_argument("--fixed", action="store_true")
    parser.add_argument("--starting-depth-bb", type=int, choices=(60, 100), default=100)
    args = parser.parse_args()
    if args.output.exists():
        raise SystemExit("Refusing to overwrite a match receipt")
    rules = MatchRules(starting_chips=args.starting_depth_bb*10,
                       ramp_every_hands=None if args.fixed else 25)
    result = MatchEvaluator(rules, args.seeds, args.opponent).evaluate()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2)+"\n", encoding="utf-8")
    print(json.dumps({key: result[key] for key in
                      ("match_count", "censored_matches", "paired_score_difference_mean",
                       "paired_normal_95_ci")}))
