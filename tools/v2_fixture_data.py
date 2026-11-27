"""Plain SDK fixtures shared by the v2/legacy behavior comparison."""

from chipzen import GameState
from chipzen.models import Card


def event(seat, action, amount, phase="preflop"):
    return {"seat": seat, "action": action, "amount": amount,
            "phase": phase, "is_timeout": False}


def make(phase, hole, board, history, *, pot, stack, villain, to_call,
         min_raise, max_raise, valid_actions, dealer=0, hand=1):
    return GameState(hand_number=hand, phase=phase,
                     hole_cards=[Card.from_str(card) for card in hole],
                     board=[Card.from_str(card) for card in board], pot=pot,
                     your_stack=stack, opponent_stacks=[villain], your_seat=0,
                     dealer_seat=dealer, to_call=to_call, min_raise=min_raise,
                     max_raise=max_raise, valid_actions=valid_actions,
                     action_history=history)


def fixtures():
    blinds = [event(0, "post_small_blind", 5), event(1, "post_big_blind", 10)]
    return [
        make("preflop", ("As", "Kd"), (), blinds, pot=15, stack=995,
             villain=990, to_call=5, min_raise=20, max_raise=1000,
             valid_actions=["fold", "call", "raise"], dealer=1),
        make("preflop", ("7h", "2c"), (), blinds + [event(0, "call", 5)],
             pot=20, stack=990, villain=990, to_call=0, min_raise=10,
             max_raise=990, valid_actions=["check", "raise"], dealer=0),
        make("preflop", ("Qh", "Qs"), (), blinds + [event(0, "raise", 25)],
             pot=40, stack=975, villain=990, to_call=15, min_raise=50,
             max_raise=990, valid_actions=["fold", "call", "raise"], dealer=1),
        make("flop", ("Ah", "Kd"), ("As", "7c", "2h"), blinds +
             [event(0, "raise", 25), event(1, "call", 15),
              event(1, "check", 0, "flop")], pot=50, stack=975, villain=975,
             to_call=0, min_raise=10, max_raise=975, valid_actions=["check", "raise"]),
        make("turn", ("Qh", "Jd"), ("Qs", "8s", "4d", "As"), blinds +
             [event(0, "raise", 25), event(1, "call", 15),
              event(1, "check", 0, "flop"), event(0, "raise", 40, "flop"),
              event(1, "call", 40, "flop"), event(1, "check", 0, "turn")],
             pot=130, stack=935, villain=935, to_call=0, min_raise=10,
             max_raise=935, valid_actions=["check", "raise"]),
        make("river", ("Qs", "5s"), ("Ah", "Js", "4s", "As", "4h"),
             blinds + [event(0, "raise", 25), event(1, "call", 15),
                       event(0, "check", 0, "flop"), event(1, "check", 0, "flop"),
                       event(0, "check", 0, "turn"), event(1, "check", 0, "turn")],
             pot=50, stack=975, villain=975, to_call=0, min_raise=10,
             max_raise=975, valid_actions=["check", "raise"]),
        make("river", ("Ah", "7d"), ("As", "8c", "4d", "2h", "9s"),
             blinds + [event(0, "raise", 25), event(1, "call", 15),
                       event(1, "raise", 150, "river")], pot=200, stack=825,
             villain=875, to_call=150, min_raise=300, max_raise=875,
             valid_actions=["fold", "call", "raise"], dealer=1),
        make("preflop", ("Ah", "Ad"), (),
             [event(0, "post_small_blind", 2), event(1, "post_big_blind", 4),
              event(0, "raise", 10)], pot=14, stack=6, villain=96, to_call=6,
             min_raise=16, max_raise=10, valid_actions=["fold", "call", "raise"],
             hand=8),
    ]

