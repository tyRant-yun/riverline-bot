"""Conditional response research for OOP non-nut flushes on double-paired rivers.

All response frequencies and line likelihoods are declared assumptions. The
complete-hand harness proves legal reach and cash-flow in the local rules;
neither component is a platform payout oracle or an online strategy gate.
"""

from collections import Counter
from functools import lru_cache
from itertools import combinations
from pathlib import Path
import hashlib
import json
import random
import statistics

from chipzen import Action

from hu_bot.poker import DECK, evaluate, features, preflop_score
from lab.match import play_hand


BOARD = ("Ah", "Js", "4s", "As", "4h")
BOARDS = (BOARD, ("Kh", "Ts", "3s", "Ks", "3h"),
          ("Qh", "9s", "5s", "Qs", "5h"))
PROFILES = ("normal", "wide_call", "low_bluff", "high_bluff", "range_miss")
POT = 7654
BET_SMALL = round(POT * .33)
BET_MEDIUM = 5052
HERO_REMAINING = 5207
CHECK_BET = round(POT * .5)


@lru_cache(maxsize=16)
def best_flush(board):
    available = [card for card in DECK if card not in board]
    return max(evaluate(tuple(hole) + board) for hole in combinations(available, 2)
               if evaluate(tuple(hole) + board)[0] == 5)


def target(board, hole):
    """General class predicate; never checks a particular hole-card string."""
    ranks = Counter(card[0] for card in board)
    rank = evaluate(tuple(hole) + tuple(board))
    return (len(board) == 5 and sorted(ranks.values()) == [1, 2, 2]
            and len(set(board) | set(hole)) == 7 and rank[0] == 5
            and rank < best_flush(tuple(board)))


def hero_combos(board):
    return [hole for hole in combinations((c for c in DECK if c not in board), 2)
            if target(board, hole)]


def villain_type(board, hero, villain):
    hero_rank = evaluate(tuple(hero) + tuple(board))
    rank = evaluate(tuple(villain) + tuple(board))
    if rank[0] >= 6:
        return "boat_or_better"
    if rank[0] == 5:
        return "higher_flush" if rank > hero_rank else "lower_flush"
    return "other_worse" if rank < hero_rank else "other_tie"


def line_mass(board, hole, profile):
    flop, turn = tuple(board[:3]), tuple(board[:4])
    pf = preflop_score(hole)
    ff = features(hole, flop).strength
    tf = features(hole, turn).strength
    mass = (.08 + .92 * pf) * (.06 + .80 * ff) * (.03 + .83 * tf)
    # Turn bet then call a check-raise: value and draws survive, with a
    # nonzero bluff/call floor rather than pretending hidden hands are known.
    mass *= .015 + .82 * tf
    if profile == "wide_call":
        mass = mass ** .72
    elif profile == "low_bluff":
        mass *= .55 + .9 * tf
    elif profile == "high_bluff":
        mass = mass ** .62
    return mass


RESPONSE = {
    "normal": {
        "boat_or_better": (0., .32, .68), "higher_flush": (.02, .73, .25),
        "lower_flush": (.18, .77, .05), "other_worse": (.68, .30, .02),
        "other_tie": (.35, .60, .05)},
    "wide_call": {
        "boat_or_better": (0., .60, .40), "higher_flush": (0., .82, .18),
        "lower_flush": (.02, .94, .04), "other_worse": (.22, .73, .05),
        "other_tie": (.12, .83, .05)},
    "low_bluff": {
        "boat_or_better": (0., .15, .85), "higher_flush": (.02, .58, .40),
        "lower_flush": (.35, .65, 0.), "other_worse": (.83, .17, 0.),
        "other_tie": (.55, .45, 0.)},
    "high_bluff": {
        "boat_or_better": (0., .35, .65), "higher_flush": (.02, .68, .30),
        "lower_flush": (.22, .60, .18), "other_worse": (.43, .30, .27),
        "other_tie": (.30, .43, .27)},
}
CHECK_BET_PROB = {
    "normal": {"boat_or_better": .76, "higher_flush": .55,
               "lower_flush": .22, "other_worse": .14, "other_tie": .18},
    "wide_call": {"boat_or_better": .60, "higher_flush": .42,
                  "lower_flush": .16, "other_worse": .10, "other_tie": .13},
    "low_bluff": {"boat_or_better": .88, "higher_flush": .52,
                  "lower_flush": .12, "other_worse": .02, "other_tie": .06},
    "high_bluff": {"boat_or_better": .75, "higher_flush": .63,
                   "lower_flush": .40, "other_worse": .46, "other_tie": .45},
}


def response(category, profile, bet):
    base = "low_bluff" if profile == "range_miss" else profile
    fold, call, raise_ = RESPONSE[base][category]
    if bet == BET_SMALL:
        shift = min(fold, .14)
        fold -= shift
        call += shift
    assert abs(fold + call + raise_ - 1) < 1e-9
    return fold, call, raise_


def posterior(board, hero, profile):
    available = [c for c in DECK if c not in board and c not in hero]
    rows = []
    for villain in combinations(available, 2):
        kind = villain_type(board, hero, villain)
        mass = line_mass(board, villain, profile)
        if profile == "range_miss" and kind == "boat_or_better":
            mass *= 3.0
        rows.append((villain, kind, mass))
    total = sum(row[2] for row in rows)
    return [(villain, kind, mass / total) for villain, kind, mass in rows]


def action_values(board, hero, profile):
    rows = posterior(board, hero, profile)
    rank = evaluate(tuple(hero) + tuple(board))
    check_fold = check_call = 0.
    results = {}
    prior = Counter()
    for villain, kind, mass in rows:
        prior[kind] += mass
        other = evaluate(tuple(villain) + tuple(board))
        win = 1. if rank > other else .5 if rank == other else 0.
        base = "low_bluff" if profile == "range_miss" else profile
        bet_probability = CHECK_BET_PROB[base][kind]
        check_fold += mass * (1 - bet_probability) * win * POT
        check_call += mass * ((1 - bet_probability) * win * POT
                              + bet_probability * (win * (POT + 2 * CHECK_BET) - CHECK_BET))
    results["check"] = {"fold_to_bet_ev": check_fold,
                        "call_half_pot_ev": check_call,
                        "best_declared_followup_ev": max(check_fold, check_call)}
    for name, bet in (("small", BET_SMALL), ("medium", BET_MEDIUM)):
        # The local HU harness caps the opponent's raise at the hero's
        # effective all-in amount. It cannot replay the platform's 7139
        # raise-to with an unmatched excess; that needs official settlement.
        raise_to = min(HERO_REMAINING, 2 * bet)
        extra_call = raise_to - bet
        masses = Counter()
        wins = Counter()
        bet_fold = bet_call = 0.
        for villain, kind, mass in rows:
            other = evaluate(tuple(villain) + tuple(board))
            win = 1. if rank > other else .5 if rank == other else 0.
            fold, call, raise_ = response(kind, profile, bet)
            masses["fold"] += mass * fold
            masses["call"] += mass * call
            masses["raise"] += mass * raise_
            wins["call"] += mass * call * win
            wins["raise"] += mass * raise_ * win
            call_payoff = win * (POT + 2 * bet) - bet
            raise_payoff = win * (POT + 2 * (bet + extra_call)) - (bet + extra_call)
            bet_fold += mass * (fold * POT + call * call_payoff - raise_ * bet)
            bet_call += mass * (fold * POT + call * call_payoff + raise_ * raise_payoff)
        results[name] = {"bet": bet, "raise_to_effective": raise_to,
                         "extra_call": extra_call, "bet_fold_ev": bet_fold,
                         "bet_call_ev": bet_call,
                         "fold_mass": masses["fold"],
                         "call_mass": masses["call"],
                         "raise_mass": masses["raise"],
                         "called_range_equity": wins["call"] / masses["call"],
                         "raised_range_equity": wins["raise"] / masses["raise"]}
    return {"prior_category_mass": dict(prior), "actions": results}


class Hero:
    def __init__(self, river, facing="call"):
        self.river, self.facing = river, facing
        self.seen = []

    def decide(self, state):
        self.seen.append({"street": state.phase, "pot": state.pot,
                          "to_call": state.to_call,
                          "history_size": len(state.action_history)})
        if state.phase == "preflop":
            return Action.call()
        if state.phase == "flop":
            return Action.call() if state.to_call else Action.check()
        if state.phase == "turn":
            return Action.raise_to(3131) if state.to_call else Action.check()
        if state.to_call:
            return Action.call() if self.facing == "call" else Action.fold()
        if self.river == "check":
            return Action.check()
        amount = BET_SMALL if self.river == "small" else BET_MEDIUM
        return Action.raise_to(max(state.min_raise, min(state.max_raise, amount)))


class Villain:
    def __init__(self, board, hero, hole, profile, river_arm, seed):
        self.board, self.hero, self.hole = board, hero, hole
        self.profile, self.river_arm = profile, river_arm
        self.u = random.Random(seed).random()
        self.river_response = None

    def decide(self, state):
        if state.phase == "preflop":
            return Action.raise_to(260)
        if state.phase == "flop":
            return Action.raise_to(436)
        if state.phase == "turn":
            return Action.call() if state.to_call else Action.raise_to(835)
        category = villain_type(self.board, self.hero, self.hole)
        if not state.to_call:
            base = "low_bluff" if self.profile == "range_miss" else self.profile
            self.river_response = "bet" if self.u < CHECK_BET_PROB[base][category] else "check"
            if self.river_response == "bet":
                return Action.raise_to(min(state.max_raise, max(state.min_raise, CHECK_BET)))
            return Action.check()
        fold, call, _ = response(category, self.profile,
                                 BET_SMALL if self.river_arm == "small" else BET_MEDIUM)
        self.river_response = "fold" if self.u < fold else "call" if self.u < fold + call else "raise"
        if self.river_response == "fold":
            return Action.fold()
        if self.river_response == "call":
            return Action.call()
        return Action.raise_to(state.max_raise)


def replay(hero, villain, profile, arm, facing, seed, board=BOARD):
    assert target(board, hero)
    assert not (set(hero) & set(villain) or set(villain) & set(board))
    deck = list(villain) + list(hero) + list(board)
    deck += [c for c in DECK if c not in deck]
    hero_player = Hero(arm, facing)
    villain_player = Villain(board, hero, villain, profile, arm, seed)
    net, _, result = play_hand([villain_player, hero_player], deck,
                               button=0, bb=100, starting_stacks=[10000, 9034])
    river = [e for e in result["action_history"] if e["phase"] == "river"]
    return {"board": board, "hero": hero, "villain": villain, "profile": profile,
            "arm": arm, "facing": facing, "seed": seed,
            "river_response": villain_player.river_response,
            "hero_river_first_state": next((s for s in hero_player.seen
                                            if s["street"] == "river" and s["to_call"] == 0), None),
            "river_actions": river, "pot_result": result["pot"],
            "refunds": result["uncalled_refunds"],
            "hero_net": net[1], "chip_total": sum(result["stacks"]),
            "turn_prefix": [e for e in result["action_history"] if e["phase"] == "turn"]}


def run(output):
    output = Path(output)
    if output.exists():
        raise FileExistsError(output)
    rows = []
    for board in BOARDS:
        for hero in hero_combos(board):
            for profile in PROFILES:
                values = action_values(board, hero, profile)
                rows.append({"board": board, "hero": hero, "profile": profile, **values})
    # Route proof covers the full screenshot board class with five hero ranks,
    # two opponent showdown categories and all river first/follow-up actions.
    witnesses = []
    for hero in (("Qs", "5s"), ("Ks", "Ts"), ("Qs", "9s"),
                 ("9s", "7s"), ("6s", "2s")):
        for villain in (("Ad", "2h"), ("Kd", "Kh"), ("Ks", "9s"), ("8s", "7s")):
            if set(hero) & set(villain):
                continue
            for profile in PROFILES:
                seed = int.from_bytes(hashlib.sha256(
                    repr((hero, villain, profile, 191101)).encode()).digest()[:8], "big")
                for arm, facing in (("check", "call"), ("small", "fold"),
                                    ("medium", "fold"), ("medium", "call")):
                    row = replay(hero, villain, profile, arm, facing, seed)
                    assert row["hero_river_first_state"]["pot"] == POT
                    assert row["chip_total"] == 19034
                    witnesses.append(row)
    for board in BOARDS[1:]:
        hero = hero_combos(board)[len(hero_combos(board)) // 2]
        by_kind = {}
        available = [c for c in DECK if c not in board and c not in hero]
        for villain in combinations(available, 2):
            by_kind.setdefault(villain_type(board, hero, villain), villain)
        for kind in ("boat_or_better", "other_worse"):
            villain = by_kind[kind]
            for profile in PROFILES:
                seed = int.from_bytes(hashlib.sha256(
                    repr((board, hero, villain, profile, 191102)).encode()).digest()[:8], "big")
                for arm, facing in (("check", "call"), ("small", "fold"),
                                    ("medium", "fold"), ("medium", "call")):
                    row = replay(hero, villain, profile, arm, facing, seed, board=board)
                    assert row["hero_river_first_state"]["pot"] == POT
                    assert row["chip_total"] == 19034
                    witnesses.append(row)
    result = {"version": "1.9.1", "board_count": len(BOARDS),
              "target_hero_combos_by_board": {" ".join(board): len(hero_combos(board))
                                              for board in BOARDS},
              "conditional_rows": rows, "complete_hand_witnesses": witnesses,
              "profile_definitions": {"responses": RESPONSE,
                                      "check_bet_probability": CHECK_BET_PROB,
                                      "range_miss": "Actual post-checkraise boat mass multiplied by 3, while normal estimate stays unchanged."},
              "rules_source": "assumed_for_research",
              "caveats": ["Line posterior and response identity are explicit hypotheses, not observed platform ranges.",
                          "Local harness caps the river raise to hero's effective stack; platform raise-to 7139 and unmatched refund require official replay.",
                          "Action values are at the river decision and use a fixed legal turn prefix; not tournament win probability."]}
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, separators=(",", ":")) + "\n", encoding="utf-8")
    summary = {"board_count": len(BOARDS), "hero_combos": result["target_hero_combos_by_board"],
               "rows": len(rows), "witnesses": len(witnesses)}
    for profile in PROFILES:
        selected = [r for r in rows if r["board"] == BOARD and r["profile"] == profile]
        summary[profile] = {"median_medium_minus_check": statistics.median(
            max(r["actions"]["medium"]["bet_call_ev"], r["actions"]["medium"]["bet_fold_ev"])
            - r["actions"]["check"]["best_declared_followup_ev"] for r in selected),
            "negative_medium_count": sum(max(r["actions"]["medium"]["bet_call_ev"],
                                             r["actions"]["medium"]["bet_fold_ev"])
                                         < r["actions"]["check"]["best_declared_followup_ev"]
                                         for r in selected)}
    return summary


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    print(json.dumps(run(args.output)))
