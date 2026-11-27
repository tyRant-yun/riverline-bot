from chipzen import GameState
from chipzen.models import Card

from hu_bot.v2_contracts import capture
from hu_bot.v2_runtime import V2Runtime


BLINDS = [
    {"seat": 0, "action": "post_small_blind", "amount": 50, "phase": "preflop"},
    {"seat": 1, "action": "post_big_blind", "amount": 100, "phase": "preflop"},
]


def event(seat, action, amount):
    return {"seat": seat, "action": action, "amount": amount, "phase": "preflop"}


def state(cards, history, *, seat, stack, villain, pot, to_call, min_raise,
          max_raise=10000, dealer=0):
    return GameState(
        hand_number=100,
        phase="preflop",
        hole_cards=[Card.from_str(card) for card in cards],
        board=[], pot=pot, your_stack=stack, opponent_stacks=[villain],
        your_seat=seat, dealer_seat=dealer, to_call=to_call,
        min_raise=min_raise, max_raise=max_raise,
        valid_actions=["fold", "call", "raise"], action_history=history,
    )


def test_v220_price_key_splits_four_preflop_nodes():
    raw = state(("As", "Kd"), BLINDS + [event(0, "raise", 250),
                 event(1, "raise", 1000), event(0, "raise", 2200)],
                seat=1, stack=9000, villain=7800, pot=3450,
                to_call=1200, min_raise=3400)
    snapshot = capture(raw, 100, (10000, 10000))
    assert snapshot.preflop_context == "VS_4BET"
    assert snapshot.node_key.preflop_price_key == "VS_4BET:D100:O250:3B1000:4B2200"
    assert (snapshot.node_key.open_to, snapshot.node_key.three_bet_to,
            snapshot.node_key.four_bet_to) == (250, 1000, 2200)
    assert not snapshot.preflop_off_grid


def test_v220_btn_open_uses_uniform_2p5_and_keeps_gray_boundaries():
    runtime = V2Runtime()
    for cards, expected_action in [
        (("Ks", "4c"), "raise"),
        (("9s", "7c"), "raise"),
        (("Qh", "8c"), "raise"),
        (("7s", "2c"), "fold"),
        (("3s", "2c"), "fold"),
        (("As", "Ad"), "call"),
    ]:
        raw = state(cards, BLINDS, seat=0, stack=9950, villain=9900,
                    pot=150, to_call=50, min_raise=200, dealer=0)
        action = runtime.decide(raw)
        assert action.action == expected_action
        if expected_action == "raise":
            assert action.amount == 250


def test_v220_reraise_candidates_are_real_integer_raise_to_values():
    runtime = V2Runtime()
    raw = state(("As", "Kd"), BLINDS + [event(0, "raise", 250),
                 event(1, "raise", 1000)], seat=0, stack=9750,
                villain=9000, pot=1250, to_call=750, min_raise=1750,
                dealer=0)
    action = runtime.decide(raw)
    trace = runtime.last_trace
    assert action.action == "raise" and action.amount == 2200
    assert trace["preflop_candidates"]["raise_to"] == [2200]

    raw = state(("As", "Ad"), BLINDS + [event(0, "raise", 250),
                 event(1, "raise", 1000), event(0, "raise", 2200)],
                seat=1, stack=9000, villain=7800, pot=3450,
                to_call=1200, min_raise=3400)
    action = runtime.decide(raw)
    trace = runtime.last_trace
    assert action.action == "raise" and action.amount == 10000
    assert trace["preflop_candidates"]["raise_to"] == [4840, 10000]


def test_v220_reverse_sentinels_do_not_auto_call_weak_structures():
    runtime = V2Runtime()
    limp = state(("Qh", "2c"), BLINDS, seat=0, stack=9950, villain=9900,
                 pot=150, to_call=50, min_raise=200, dealer=0)
    assert runtime.decide(limp).action == "fold"

    iso = state(("Qh", "2c"), BLINDS + [event(0, "call", 50),
                event(1, "raise", 500)], seat=0, stack=9900, villain=9500,
                pot=600, to_call=400, min_raise=900, dealer=0)
    assert runtime.decide(iso).action == "fold"

    weak_4bet = state(("6s", "5s"), BLINDS + [event(0, "raise", 250),
                       event(1, "raise", 1000), event(0, "raise", 2200)],
                      seat=1, stack=9000, villain=7800, pot=3450,
                      to_call=1200, min_raise=3400)
    action = runtime.decide(weak_4bet)
    assert action.action == "fold"
    assert runtime.last_trace["preflop_candidates"]["raise_to"] == [4840, 10000]

    protected = state(("As", "Ad"), BLINDS + [event(0, "call", 50),
                   event(1, "raise", 500)], seat=0, stack=9900, villain=9500,
                   pot=600, to_call=400, min_raise=900, dealer=0)
    action = runtime.decide(protected)
    assert action.action == "raise" and action.amount == 1500
    assert runtime.last_trace["snapshot"]["preflop_context"] == "VS_LIMP_RAISE"


def test_v220_pdf_slice_requires_zero_ante_and_80_to_120bb_depth():
    runtime = V2Runtime()
    for depth, expected_action, expected_reason in (
            (79, "raise", "button_first_in_baseline"),
            (80, "call", "pdf_pressure_limp_protection_mix"),
            (120, "call", "pdf_pressure_limp_protection_mix"),
            (121, "raise", "button_first_in_baseline")):
        raw = state(("As", "Ad"), BLINDS, seat=0, stack=depth * 100 - 50,
                    villain=depth * 100 - 100, pot=150, to_call=50,
                    min_raise=200, max_raise=depth * 100, dealer=0)
        action = runtime.decide(raw)
        assert action.action == expected_action
        assert runtime.last_trace["policy"]["reason"] == expected_reason

    ante_history = [
        {"seat": 0, "action": "post_ante", "amount": 10, "phase": "preflop"},
        {"seat": 1, "action": "post_ante", "amount": 10, "phase": "preflop"},
    ] + BLINDS
    raw = state(("As", "Ad"), ante_history, seat=0, stack=9950,
                villain=9900, pot=170, to_call=50, min_raise=200, dealer=0)
    runtime.decide(raw)
    assert runtime.last_trace["snapshot"]["preflop_context"] == "FIRST_IN"
    assert runtime.last_trace["policy"]["reason"] == "button_first_in_baseline"
