from chipzen import GameState
from chipzen.models import Card

from hu_bot.v2_runtime import V2Runtime


def test_facts_failure_uses_raw_legal_menu_instead_of_sdk_fixed_fold():
    raw = GameState(hand_number=1, phase="flop",
                    hole_cards=[Card.from_str("As"), Card.from_str("Kd")],
                    board=[Card.from_str("2c"), Card.from_str("7h"), Card.from_str("9d")],
                    pot=20, your_stack=990, opponent_stacks=[990], your_seat=0,
                    dealer_seat=0, to_call=0, min_raise=10, max_raise=990,
                    valid_actions=["check", "raise"],
                    action_history=[{"seat": 1, "action": "check", "amount": "bad",
                                     "phase": "flop", "is_timeout": False}])
    runtime = V2Runtime()
    action = runtime.decide(raw)
    assert action.action == "check"
    assert runtime.last_trace["policy"]["reason"] == "raw_facts_safe_baseline"
    assert runtime.last_trace["node_key"]["digest"] is None
