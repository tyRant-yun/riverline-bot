from dataclasses import replace

from hu_bot.config import Config
from hu_bot.engine import Engine
from lab.conditional_eval import _frozen_v13_package
from lab.scenarios import SRP, entry, spot


def river_after_check():
    return spot("Ah Qd", "As 8c 4d 2h 9s",
                action_history=SRP+[entry(1, "check", 0, "river")])


def facing_river():
    return spot("Ah 8d", "As 8c 4d 2h 9s", pot=250, to_call=200,
                opponent_stacks=[775], min_raise=400, max_raise=975,
                valid_actions=["fold", "call", "raise"],
                action_history=SRP+[entry(1, "raise", 200, "river")])


def engine(raw, **changes):
    bot = Engine(replace(Config(trace=False), **changes))
    bot.evaluate(raw)
    return bot


def test_default_flags_match_frozen_v13_on_both_river_branches():
    frozen = _frozen_v13_package()
    assert not Config().unpriced_raise_guard and not Config().conditional_value_enabled
    for raw in (river_after_check(), facing_river()):
        candidate = engine(raw)
        reference = frozen.Engine(frozen.Config(trace=False))
        reference.evaluate(raw)
        assert candidate.last_trace["policy"] == reference.last_trace["policy"]
        assert candidate.last_trace["action"] == reference.last_trace["action"]
        assert not candidate.last_trace["unpriced_raise_guard"]["applied"]
        assert not candidate.last_trace["conditional_value"]["applied"]


def test_opt_in_guard_reaches_engine_and_replay_preserves_action():
    raw = facing_river()
    bot = engine(raw, unpriced_raise_guard=True)
    trace = bot.last_trace
    assert trace["unpriced_raise_guard"]["applied"]
    assert trace["policy"] == {"CALL": 1.}
    bot.evaluate(raw, replay_trace=trace)
    assert bot.last_trace["policy"] == trace["policy"]
    assert bot.last_trace["action"] == trace["action"]


def test_opt_in_value_reaches_engine_only_after_real_response_opportunities():
    raw = river_after_check()
    bot = Engine(replace(Config(trace=False), conditional_value_enabled=True))
    bot.evaluate(raw)
    assert not bot.last_trace["conditional_value"]["applied"]
    assert bot.last_trace["conditional_value"]["reason"] == "insufficient_context_response_evidence"
    history = SRP+[entry(1, "check", 0, "river"),
                   entry(0, "raise", 16, "river"),
                   entry(1, "call", 16, "river")]
    for hand in range(100):
        bot.opponent.observe_hand(("value", hand), {"action_history": history}, 0, 0)
    bot.evaluate(raw)
    trace = bot.last_trace
    assert trace["conditional_value"]["applied"]
    assert trace["conditional_value"]["selected"] == "BET_SMALL"
    assert trace["policy"] == {"BET_SMALL": 1.}
    assert not trace["fallback"]
    bot.evaluate(raw, replay_trace=trace)
    assert bot.last_trace["policy"] == trace["policy"]
    assert bot.last_trace["action"] == trace["action"]


def test_compact_trace_does_not_include_profile_or_value_details():
    bot = engine(river_after_check(), retain_diagnostics=False,
                 unpriced_raise_guard=True, conditional_value_enabled=True)
    assert not bot.last_trace["diagnostics_retained"]
    assert "conditional_value" not in bot.last_trace
    assert "unpriced_raise_guard" not in bot.last_trace
    assert "opponent" not in bot.last_trace
