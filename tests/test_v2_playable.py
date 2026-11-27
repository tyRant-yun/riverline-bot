from chipzen import GameState
from chipzen.models import Card

from hu_bot.v2_contracts import capture
from hu_bot.v2_runtime import V2Runtime


def event(seat, action, amount, phase="preflop"):
    return {"seat": seat, "action": action, "amount": amount,
            "phase": phase, "is_timeout": False}


def state(phase, hole, board, history, *, pot, stack, villain,
          to_call, min_raise, max_raise, valid_actions, dealer=0, your_seat=0, hand=1):
    return GameState(hand_number=hand, phase=phase,
                     hole_cards=[Card.from_str(card) for card in hole],
                     board=[Card.from_str(card) for card in board], pot=pot,
                     your_stack=stack, opponent_stacks=[villain], your_seat=your_seat,
                     dealer_seat=dealer, to_call=to_call, min_raise=min_raise,
                     max_raise=max_raise, valid_actions=valid_actions,
                     action_history=history)


def fixtures():
    blinds = [event(0, "post_small_blind", 5), event(1, "post_big_blind", 10)]
    return [
        state("preflop", ("As", "Kd"), (), blinds, pot=15, stack=995,
              villain=990, to_call=5, min_raise=20, max_raise=1000,
              valid_actions=["fold", "call", "raise"], dealer=1),
        state("preflop", ("7h", "2c"), (), blinds + [event(0, "call", 5)],
              pot=20, stack=990, villain=990, to_call=0, min_raise=10,
              max_raise=990, valid_actions=["check", "raise"], dealer=0),
        state("preflop", ("Qh", "Qs"), (), blinds + [event(0, "raise", 25)],
              pot=40, stack=975, villain=990, to_call=15, min_raise=50,
              max_raise=990, valid_actions=["fold", "call", "raise"], dealer=1),
        state("flop", ("Ah", "Kd"), ("As", "7c", "2h"), blinds +
              [event(0, "raise", 25), event(1, "call", 15),
               event(1, "check", 0, "flop")], pot=50, stack=975, villain=975,
              to_call=0, min_raise=10, max_raise=975, valid_actions=["check", "raise"]),
        state("turn", ("Qh", "Jd"), ("Qs", "8s", "4d", "As"), blinds +
              [event(0, "raise", 25), event(1, "call", 15),
               event(1, "check", 0, "flop"), event(0, "raise", 40, "flop"),
               event(1, "call", 40, "flop"), event(1, "check", 0, "turn")],
              pot=130, stack=935, villain=935, to_call=0, min_raise=10,
              max_raise=935, valid_actions=["check", "raise"]),
        state("river", ("Qs", "5s"), ("Ah", "Js", "4s", "As", "4h"),
              blinds + [event(0, "raise", 25), event(1, "call", 15),
                        event(0, "check", 0, "flop"), event(1, "check", 0, "flop"),
                        event(0, "check", 0, "turn"), event(1, "check", 0, "turn")],
              pot=50, stack=975, villain=975, to_call=0, min_raise=10,
              max_raise=975, valid_actions=["check", "raise"]),
        state("river", ("9h", "8h"), ("As", "8c", "4d", "2h", "9s"),
              blinds + [event(0, "raise", 25), event(1, "call", 15),
                        event(1, "raise", 150, "river")], pot=200, stack=825,
              villain=875, to_call=150, min_raise=300, max_raise=875,
              valid_actions=["fold", "call", "raise"], dealer=1),
        state("preflop", ("Ah", "Ad"), (),
              [event(0, "post_small_blind", 2), event(1, "post_big_blind", 4),
               event(0, "raise", 10)], pot=14, stack=6, villain=96, to_call=6,
              min_raise=16, max_raise=10, valid_actions=["fold", "call", "raise"],
              dealer=0, hand=8),
    ]


def test_v2_has_actual_legal_actions_across_streets_and_depths():
    runtime = V2Runtime()
    for raw in fixtures():
        action = runtime.decide(raw)
        assert action.action in raw.valid_actions
        if action.action == "raise":
            assert raw.min_raise <= action.amount <= raw.max_raise
        assert runtime.last_trace["path"] == "v2"
        assert runtime.last_trace["information_set"] == "hero_cards_plus_public_state_only"
        assert runtime.last_trace["policy"]["source"] == "v2_conservative_baseline"


def test_v2_node_key_separates_price_depth_and_rule_facts():
    first = capture(fixtures()[0], 10)
    changed = capture(state("preflop", ("As", "Kd"), (), fixtures()[0].action_history,
                           pot=15, stack=995, villain=990, to_call=5,
                           min_raise=30, max_raise=1000,
                           valid_actions=["fold", "call", "raise"], dealer=1), 10)
    assert first.node_key.digest != changed.node_key.digest
    assert first.node_key.rule_source == "sdk_authority+action_history"
    assert first.ledger.matched_contestable_pot <= first.ledger.displayed_pot


def test_v2_does_not_claim_action_ev_or_read_villain_cards():
    runtime = V2Runtime()
    runtime.decide(fixtures()[5])
    trace = runtime.last_trace
    assert trace["action_value"]["status"] == "model_limited"
    assert "villain_cards" not in trace
    assert trace["snapshot"]["valid_actions"]


def test_v2_paired_board_non_nut_flush_is_not_auto_value_bet():
    runtime = V2Runtime()
    action = runtime.decide(fixtures()[5])
    assert action.action == "check"
    assert runtime.last_trace["policy"]["reason"] == "paired_board_non_nut_flush_caution"

    facing = state("river", ("Qs", "5s"), ("Ah", "Js", "4s", "As", "4h"),
                   fixtures()[5].action_history + [event(1, "raise", 150, "river")],
                   pot=200, stack=825, villain=875, to_call=150,
                   min_raise=300, max_raise=875,
                   valid_actions=["fold", "call", "raise"], dealer=0)
    action = runtime.decide(facing)
    assert action.action == "fold"
    assert runtime.last_trace["policy"]["reason"] == "paired_board_non_nut_flush_caution"


def test_v2_conflicting_or_missing_price_history_never_hits_normal_route():
    raw = state("preflop", ("Jc", "3c"), (),
                 [event(0, "post_small_blind", 5), event(1, "post_big_blind", 10),
                 event(0, "raise", 20)], pot=110, stack=900, villain=800,
                 to_call=90, min_raise=190, max_raise=900,
                valid_actions=["fold", "call", "raise"], dealer=0, your_seat=1)
    runtime = V2Runtime()
    action = runtime.decide(raw)
    assert runtime.last_trace["ledger"]["status"] == "conflict"
    assert runtime.last_trace["policy"]["reason"] == "ledger_conflict_safe_baseline"
    assert action.action == "fold"

    missing = state("preflop", ("Jc", "3c"), (), [], pot=110, stack=900,
                    villain=800, to_call=90, min_raise=190, max_raise=900,
                    valid_actions=["fold", "call", "raise"], dealer=0)
    runtime.decide(missing)
    assert runtime.last_trace["ledger"]["status"] == "incomplete_history"
    assert runtime.last_trace["policy"]["model_status"] == "unscorable"

