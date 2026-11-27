from dataclasses import replace
import json
import logging

from bot import HUBot
from hu_bot.config import Config
from hu_bot.engine import Engine
from lab.scenarios import scenarios


def test_uploaded_entry_keeps_no_replay_or_decision_logs(caplog):
    bot = HUBot()
    state = scenarios()["river_giveup"]
    with caplog.at_level(logging.INFO):
        bot.decide(state)
    assert not bot.engine.config.trace
    assert not bot.engine.config.retain_diagnostics
    assert not any("TRACE " in r.message or "DECIDE " in r.message for r in caplog.records)
    for field in ("replay", "hole", "board", "hero_range", "villain_range", "river_ev"):
        assert field not in bot.engine.last_trace
    assert len(json.dumps(bot.engine.last_trace)) < 1024


def test_match_local_memory_is_idempotent_and_cleared_at_boundaries():
    bot = HUBot()
    bot.on_match_start({"match_id": "one", "game_config": {"big_blind": 10}})
    old_model = bot.engine.opponent
    for index in range(300):
        bot.on_round_result({"round_id": str(index), "result": {
            "action_history": [], "showdown": [{"seat": 1, "hole_cards": ["Ah", "Kd"]}]}})
    assert len(old_model.showdowns) == 64
    assert len(old_model._seen) == 300
    bot.on_round_start({"state": {"big_blind": 20, "hand_number": 300}})
    assert bot.engine.big_blind == 20
    bot.on_match_end({})
    assert bot.engine is None
    assert set(bot.last_match_summary) == {
        "match", "hands", "decisions", "fallbacks", "last_big_blind",
        "version", "table_sha256", "range_spec_sha256", "counters"}
    assert "hole_cards" not in str(bot.last_match_summary)
    bot.on_match_start({"match_id": "two", "game_config": {"big_blind": 5}})
    assert bot.engine.opponent is not old_model
    assert not bot.engine.opponent.stats and not bot.engine.opponent.showdowns
    assert bot.last_match_summary is None
    assert bot.engine.big_blind == 5


def test_uploaded_errors_are_bounded_without_full_tracebacks(caplog):
    bot = HUBot()
    bad = replace(scenarios()["flop_combo_draw"], opponent_stacks=[1000, 1000])
    with caplog.at_level(logging.ERROR):
        for _ in range(10):
            bot.decide(bad)
    errors = [record for record in caplog.records if record.name == "hu-v1"]
    assert len(errors) == 3
    assert all(record.exc_info is None for record in errors)
    assert bot.engine.fallbacks == 10
    assert "replay" not in bot.engine.last_trace


def test_offline_engine_retains_explicit_diagnostics():
    engine = Engine(Config(trace=False))
    engine.decide(scenarios()["flop_combo_draw"])
    assert "replay" in engine.last_trace and "attribution" in engine.last_trace
