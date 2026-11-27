import time

from chipzen import GameState
from chipzen.models import Card

from hu_bot import v2_runtime
from hu_bot.v2_runtime import V2Runtime


def event(seat, action, amount, phase="preflop"):
    return {"seat": seat, "action": action, "amount": amount,
            "phase": phase, "is_timeout": False}


def test_parent_returns_legal_safe_action_when_policy_worker_is_late(monkeypatch):
    raw = GameState(hand_number=1, phase="preflop",
                    hole_cards=[Card.from_str("As"), Card.from_str("Kd")], board=[],
                    pot=15, your_stack=995, opponent_stacks=[990], your_seat=0,
                    dealer_seat=0, to_call=5, min_raise=20, max_raise=995,
                    valid_actions=["fold", "call", "raise"],
                    action_history=[event(0, "post_small_blind", 5),
                                    event(1, "post_big_blind", 10)])
    original = v2_runtime.choose

    def slow_choose(snapshot):
        time.sleep(.12)
        return original(snapshot)

    monkeypatch.setattr(v2_runtime, "choose", slow_choose)
    runtime = V2Runtime()
    runtime.reset(timeout_ms=20)
    started = time.perf_counter()
    action = runtime.decide(raw)
    elapsed_ms = (time.perf_counter() - started) * 1000
    assert elapsed_ms < 100
    assert action.action in raw.valid_actions
    assert runtime.last_trace["fallback_reason"] == "local_deadline"
    assert runtime.last_trace["action_value"]["status"] == "model_limited"
