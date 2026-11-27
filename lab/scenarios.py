from chipzen import GameState
from chipzen.models import Card


def entry(seat, action, amount=0, phase="preflop"):
    return {"seat": seat, "action": action, "amount": amount, "phase": phase,
            "is_timeout": False}


BLINDS = [entry(0, "post_small_blind", 5), entry(1, "post_big_blind", 10)]
SRP = BLINDS+[entry(0, "raise", 25), entry(1, "call", 15)]


def spot(hole="Ah Kd", board="", **kwargs):
    cards = board.split()
    phase = {0: "preflop", 3: "flop", 4: "turn", 5: "river"}[len(cards)]
    values = dict(hand_number=1, phase=phase,
                  hole_cards=[Card.from_str(c) for c in hole.split()],
                  board=[Card.from_str(c) for c in cards],
                  pot=15 if not cards else 50, your_stack=995 if not cards else 975,
                  opponent_stacks=[990 if not cards else 975], your_seat=0, dealer_seat=0,
                  to_call=5 if not cards else 0, min_raise=20 if not cards else 10,
                  max_raise=1000 if not cards else 975,
                  valid_actions=["fold", "call", "raise"] if not cards else ["check", "raise"],
                  action_history=list(BLINDS if not cards else SRP))
    values.update(kwargs)
    return GameState(**values)


def scenarios():
    result = {
        "btn_premium": spot("Ah Ad"), "btn_trash": spot("7h 2d"),
        "btn_connector": spot("8s 7s"),
        "bb_vs_minraise": spot("Qh 8d", your_seat=1, pot=30, your_stack=990,
            opponent_stacks=[980], to_call=10, min_raise=30,
            action_history=BLINDS+[entry(0, "raise", 20)]),
        "bb_vs_large_open": spot("7h 2d", your_seat=1, pot=110, your_stack=990,
            opponent_stacks=[900], to_call=90, min_raise=190,
            action_history=BLINDS+[entry(0, "raise", 100)]),
        "btn_vs_3bet": spot("Ah Qh", pot=120, your_stack=970,
            opponent_stacks=[910], to_call=60, min_raise=150,
            action_history=BLINDS+[entry(0, "raise", 30), entry(1, "raise", 90)]),
        "ten_bb_jam": spot("8h 8d", your_stack=95, opponent_stacks=[90], max_raise=100),
        "flop_nuts": spot("Ah Ad", "As 8s 4d"),
        "flop_top_pair": spot("Ah Qd", "As 8c 4d"),
        "flop_second_pair": spot("8h 7d", "As 8c 4d"),
        "flop_combo_draw": spot("9s 8s", "As 7s 6d"),
        "flop_air_dry": spot("9h 6d", "As 8c 3d"),
        "flop_air_wet": spot("Ah 2d", "9s 8s 7d"),
        "flop_monotone": spot("Ah Ad", "Ks 9s 4s"),
        "turn_value": spot("Ah Ad", "As 8s 4d 2c"),
        "turn_scare": spot("Qh Jd", "Qs 8s 4d As"),
        "turn_completed_draw": spot("Js Ts", "9s 8s 2d Qs"),
        "turn_giveup": spot("9h 6d", "As 8c 3d Kc"),
        "river_nuts": spot("As Ks", "Qs Js Ts 2h 3d"),
        "river_thin_value": spot("Ah Qd", "As 8c 4d 2h 9s"),
        "river_bluff_catcher": spot("Ah 7d", "As 8c 4d 2h 9s", pot=150,
            to_call=100, min_raise=200, opponent_stacks=[875], valid_actions=["fold", "call", "raise"],
            action_history=SRP+[entry(1, "raise", 100, "river")]),
        "river_missed_draw": spot("9s 8s", "As 7s 6d Kc 2h"),
        "river_giveup": spot("7h 2d", "As Kc Qd 9h 4s"),
        "river_board_royal": spot("7h 2d", "As Ks Qs Js Ts"),
    }
    return result
