"""Small HU cash-hand duplicate harness, deliberately independent of strategy sizing.

Fixed stacks per hand; not a reproduction of the Chipzen tournament engine.
The two rotations share hole cards, button, and board. CI clusters by duplicate pair.
"""
from dataclasses import replace
import inspect
import random
import statistics
from math import sqrt
from time import perf_counter

from chipzen import Action, GameState
from chipzen.models import Card
from treys import Card as OracleCard, Evaluator

from hu_bot.config import Config
from hu_bot.engine import Engine
from hu_bot.poker import DECK, preflop_score, features
from lab.scenarios import entry

ORACLE = Evaluator()


class Baseline:
    def __init__(self, name, seed):
        self.name, self.rng = name, random.Random(seed)

    def decide(self, state):
        valid = state.valid_actions
        passive = Action.check() if "check" in valid else Action.call()
        if self.name == "call":
            return passive
        hole, board = tuple(map(str, state.hole_cards)), tuple(map(str, state.board))
        strength = features(hole, board).strength if board else preflop_score(hole)
        if self.name == "random":
            choice = self.rng.choice(valid)
            return Action.raise_to(self.rng.randint(state.min_raise, state.max_raise)) if choice == "raise" else Action(choice)
        if self.name == "aggressive":
            if "raise" in valid and self.rng.random() < .6:
                target = state.min_raise+round(state.pot*.6)
                return Action.raise_to(min(state.max_raise, target))
            return passive if "check" in valid or strength > .35 else Action.fold()
        if strength > .72 and "raise" in valid:
            return Action.raise_to(min(state.max_raise, max(state.min_raise, int(state.pot*.66))))
        if "check" in valid:
            return Action.check()
        return Action.call() if strength > .50 and state.to_call <= state.pot*.40 else Action.fold()


def play_hand(players, deck, hand_number=1, button=0, stack=1000, bb=10, ante=0,
              starting_stacks=None):
    holes, board = [deck[:2], deck[2:4]], deck[4:9]
    starts = [stack, stack] if starting_stacks is None else list(starting_stacks)
    if len(starts) != 2 or min(starts) <= 0:
        raise ValueError("Two positive starting stacks required")
    total_chips = sum(starts)
    stacks = [max(0, amount-ante) for amount in starts]
    pot = 2*ante
    history = []
    posted = {}
    for seat, amount, label in ((button, bb//2, "post_small_blind"), (1-button, bb, "post_big_blind")):
        amount = min(stacks[seat], amount)
        stacks[seat] -= amount
        pot += amount
        posted[seat] = amount
        history.append(entry(seat, label, amount))
    if ante:
        history.extend(entry(seat, "post_ante", ante) for seat in (0, 1))
    bets = [0, 0]
    bets[button], bets[1-button] = posted[button], posted[1-button]
    winner, decisions, shown = None, [], []
    for street, count in (("preflop", 0), ("flop", 3), ("turn", 4), ("river", 5)):
        if street != "preflop":
            bets = [0, 0]
        if min(stacks) == 0:
            break
        actor = button if street == "preflop" else 1-button
        pending, last_raise = {0, 1}, bb
        steps = 0
        while pending:
            steps += 1
            if steps > 100:
                raise AssertionError("Non-terminating betting round")
            other = 1-actor
            owed = max(bets)-bets[actor]
            to_call = min(owed, stacks[actor])
            valid = ["fold", "call"] if owed else ["check"]
            maximum = min(bets[actor]+stacks[actor], bets[other]+stacks[other])
            minimum = min(maximum, max(bets)+last_raise)
            if stacks[other] > 0 and stacks[actor] > owed and maximum > max(bets):
                valid.append("raise")
            state = GameState(hand_number=hand_number, phase=street,
                hole_cards=[Card.from_str(c) for c in holes[actor]],
                board=[Card.from_str(c) for c in board[:count]], pot=pot,
                your_stack=stacks[actor], opponent_stacks=[stacks[other]],
                your_seat=actor, dealer_seat=button, to_call=to_call,
                min_raise=minimum if "raise" in valid else 0,
                max_raise=maximum if "raise" in valid else 0,
                valid_actions=valid, action_history=list(history))
            action = players[actor].decide(state)
            assert action.action in valid, (action, state)
            if hasattr(players[actor], "last_trace"):
                decisions.append(players[actor].last_trace.copy())
            if action.action == "fold":
                history.append(entry(actor, "fold", 0, street))
                winner = other
                break
            if action.action == "raise":
                assert minimum <= action.amount <= maximum
                increment = action.amount-bets[actor]
                last_raise = max(last_raise, action.amount-max(bets))
                bets[actor] = action.amount
                pending = {other}
                amount = action.amount
            elif action.action == "call":
                increment, amount = to_call, to_call
                bets[actor] += increment
                pending.discard(actor)
            else:
                increment, amount = 0, 0
                pending.discard(actor)
            pot += increment
            stacks[actor] -= increment
            history.append(entry(actor, action.action, amount, street))
            assert min(stacks) >= 0 and sum(stacks)+pot == total_chips
            if stacks[actor] == 0 and bets[actor] == bets[other]:
                pending.clear()
            actor = other
        if winner is not None:
            break
    uncalled_refunds = [0, 0]
    if winner is None:
        # HU showdown cannot award chips the shorter player never matched.
        committed = [starts[i]-stacks[i] for i in (0, 1)]
        if committed[0] != committed[1]:
            rich = 0 if committed[0] > committed[1] else 1
            refund = abs(committed[0]-committed[1])
            stacks[rich] += refund
            pot -= refund
            uncalled_refunds[rich] = refund
        ranks = [ORACLE.evaluate([OracleCard.new(c) for c in board],
                                 [OracleCard.new(c) for c in h]) for h in holes]
        shown = [{"seat": i, "hole_cards": holes[i], "hand_rank": ranks[i]} for i in (0, 1)]
        if ranks[0] == ranks[1]:
            payouts = [pot//2, pot//2]
            payouts[1-button] += pot % 2
            winners = [0, 1]
        else:
            winner = 0 if ranks[0] < ranks[1] else 1
    if winner is not None:
        payouts = [0, 0]
        payouts[winner] = pot
        winners = [winner]
    for i in (0, 1):
        stacks[i] += payouts[i]
    assert sum(stacks) == total_chips
    result = {"winner_seats": winners, "pot": pot, "action_history": history,
              "uncalled_refunds": uncalled_refunds,
              "showdown": shown, "board": board,
              "payouts": [{"seat": i, "amount": payouts[i]} for i in (0, 1)], "stacks": stacks}
    for i, player in enumerate(players):
        if hasattr(player, "opponent"):
            observe = player.opponent.observe_hand
            if "big_blind" in inspect.signature(observe).parameters:
                observe(hand_number, result, i, button, board,
                        big_blind=bb, depth_bb=min(starts)/bb)
            else:
                observe(hand_number, result, i, button, board)
    return [x-starts[i] for i, x in enumerate(stacks)], decisions, result


def benchmark(pairs=100, opponent="tight", seed=42):
    from lab.cli import analyze
    if pairs < 2:
        raise ValueError("At least two duplicate pairs required for a confidence interval")
    rng, pair_results, traces = random.Random(seed), [], []
    start = perf_counter()
    # Same production policy, full sample count. Independent memory streams per rotation.
    config = replace(Config(), trace=False)
    bots = [Engine(config), Engine(config)]
    villains = [Baseline(opponent, seed+1), Baseline(opponent, seed+1)]
    for pair in range(pairs):
        deck = rng.sample(DECK, 52)
        outcomes = []
        for hero_seat in (0, 1):
            players = [villains[hero_seat], villains[hero_seat]]
            players[hero_seat] = bots[hero_seat]
            net, decisions, _ = play_hand(players, deck, pair+1, button=pair % 2)
            outcomes.append(net[hero_seat]/10)
            traces.extend(decisions)
        pair_results.append(sum(outcomes)/2)
    mean = statistics.mean(pair_results)
    se = statistics.stdev(pair_results)/sqrt(pairs)
    return {"opponent": opponent, "seed": seed, "pairs": pairs, "hands": 2*pairs,
            "bb_per_100": mean*100, "normal_95_ci_bb_per_100": [(mean-1.96*se)*100, (mean+1.96*se)*100],
            "pair_net_bb_mean": pair_results, "elapsed_seconds": round(perf_counter()-start, 2),
            "diagnostics": analyze(traces),
            "limitations": "Fixed 100BB cash hands against simple scripted baselines. Paired normal CI; no tournament win-rate, GTO strength, or exploitability claim."}
