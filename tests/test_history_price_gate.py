"""The promoted table must never price an incomplete preflop ledger cheaply."""
from dataclasses import replace

from chipzen import GameState
from chipzen.models import Card
import pytest

from hu_bot.config import Config
from hu_bot.core import normalize
from hu_bot.engine import Engine
from hu_bot.preflop import preflop_policy
from hu_bot.range_spec import candidate_status
from bot import HUBot


def _event(seat, action, amount):
    return {"seat": seat, "action": action, "amount": amount,
            "phase": "preflop", "is_timeout": False}


def _spot(context, depth, price=0, cards=("Qh", "8d"), hand=1):
    bb, sb = 100, 50
    history = [_event(0, "post_small_blind", sb),
               _event(1, "post_big_blind", bb)]
    bets = [sb, bb]
    if context == "BB_VS_LIMP":
        history.append(_event(0, "call", sb))
        bets[0] = bb
    elif context == "BB_VS_OPEN":
        bets[0] = round(price*bb)
        history.append(_event(0, "raise", bets[0]))
    elif context != "BTN_FIRST_IN":
        raise ValueError(context)
    seat = 0 if context == "BTN_FIRST_IN" else 1
    owed = max(0, bets[1-seat]-bets[seat])
    chips = depth*bb
    return GameState(hand_number=hand, phase="preflop",
                     hole_cards=[Card.from_str(card) for card in cards], board=[],
                     pot=sum(bets), your_stack=chips-bets[seat],
                     opponent_stacks=[chips-bets[1-seat]], your_seat=seat,
                     dealer_seat=0, to_call=owed,
                     min_raise=max(bets)+bb, max_raise=chips,
                     valid_actions=["fold", "call", "raise"] if owed else
                                   ["check", "raise"], action_history=history)


def _pair(raw):
    settings = Config(seed=983368, trace=False, retain_diagnostics=True)
    enabled, old = Engine(settings), Engine(replace(settings, range_repair_enabled=False))
    enabled.reset("history-price-gate", 100)
    old.reset("history-price-gate", 100)
    action, old_action = enabled.decide(raw), old.decide(raw)
    return action, old_action, enabled.last_trace, old.last_trace


@pytest.mark.parametrize("context,depth,price", [
    ("BTN_FIRST_IN", 60, 0), ("BB_VS_LIMP", 100, 1),
    ("BB_VS_OPEN", 60, 2), ("BB_VS_OPEN", 60, 2.25),
])
def test_four_promoted_windows_keep_their_complete_history_policy(context, depth, price):
    raw = _spot(context, depth, price)
    policy, status = candidate_status(normalize(raw, 100))
    assert status == "applied" and policy is not None
    action, _, trace, _ = _pair(raw)
    assert trace["preflop_range"]["applied"] is True
    assert trace["preflop_range"]["status"] == "applied"
    assert action.action in raw.valid_actions


def test_stale_open_price_uses_the_old_chart_and_reports_reason():
    raw = _spot("BB_VS_OPEN", 60, 2, ("Jc", "3c"))
    stale = replace(raw, pot=1100, to_call=900, min_raise=1900,
                    opponent_stacks=[5000])
    state = normalize(stale, 100)
    assert state.villain_bet == 1000
    assert candidate_status(state) == (None, "history_price_mismatch")
    assert preflop_policy(state, use_range_spec=True).weights == preflop_policy(
        state, use_range_spec=False).weights
    action, old_action, trace, old_trace = _pair(stale)
    assert action.to_dict() == old_action.to_dict()
    assert trace["policy"] == old_trace["policy"]
    assert trace["preflop_range"]["status"] == "history_price_mismatch"
    assert trace["preflop_range"]["applied"] is False
    assert trace["fallback"] is None


def test_missing_btn_raise_does_not_look_like_first_in():
    raw = _spot("BTN_FIRST_IN", 60)
    stale = replace(raw, pot=raw.pot+950, to_call=1000, min_raise=2000,
                    opponent_stacks=[raw.opponent_stacks[0]-1000])
    assert candidate_status(normalize(stale, 100)) == (None, "history_price_mismatch")
    action, old_action, trace, old_trace = _pair(stale)
    assert action.to_dict() == old_action.to_dict()
    assert trace["policy"] == old_trace["policy"]
    assert trace["preflop_range"]["status"] == "history_price_mismatch"


def test_preflop_pot_conflict_cannot_use_promoted_table():
    raw = _spot("BB_VS_OPEN", 60, 2.25)
    conflicted = replace(raw, pot=raw.pot + 100)
    assert candidate_status(normalize(conflicted, 100)) == (None, "history_price_mismatch")
    action, old_action, trace, _ = _pair(conflicted)
    assert action.to_dict() == old_action.to_dict()
    assert trace["preflop_range"]["status"] == "history_price_mismatch"


def test_normal_entry_keeps_bounded_range_counters_without_cards():
    engine = Engine(Config(trace=False, retain_diagnostics=False))
    engine.reset("bounded", 100)
    raw = _spot("BB_VS_OPEN", 60, 2.25)
    for _ in range(3):
        engine.decide(raw)
    assert engine.match_counters["range_applied"] == 3
    assert engine.last_trace["preflop_range"]["status"] == "applied"
    assert "hole" not in engine.last_trace
    assert len(engine.match_counters) <= 8


@pytest.mark.parametrize("context,price", [("BB_VS_LIMP", 1),
                                           ("BB_VS_OPEN", 2.5)])
def test_unpromoted_sixty_bb_windows_are_experiment_only(context, price):
    raw = _spot(context, 60, price)
    state = normalize(raw, 100)
    assert candidate_status(state) == (None, "out_of_scope")
    assert candidate_status(state, include_research=True)[1] == "applied"
    baseline = Engine(Config(trace=False, retain_diagnostics=True))
    trial = Engine(Config(trace=False, retain_diagnostics=True,
                          range_extension_enabled=True))
    baseline.decide(raw)
    trial.decide(raw)
    assert baseline.last_trace["preflop_range"]["applied"] is False
    assert trial.last_trace["preflop_range"]["applied"] is True
    assert trial.last_trace["preflop_range"]["research_window"] is True


@pytest.mark.parametrize("depth,price", [(60, 2.49), (60, 2.51),
                                        (54, 2.5), (66, 2.5)])
def test_research_price_and_depth_boundaries_fall_back(depth, price):
    raw = _spot("BB_VS_OPEN", depth, price)
    assert candidate_status(normalize(raw, 100), include_research=True) == (
        None, "out_of_scope")


def test_match_summary_contains_version_digests_and_bounded_counts():
    bot = HUBot()
    bot.on_match_start({"match_id": "summary", "game_config": {"big_blind": 100},
                        "seats": [{"seat": 1, "is_self": True}]})
    bot.decide(_spot("BB_VS_OPEN", 60, 2.25))
    bot.on_round_result({"round_id": "one", "result": {"hand_number": 1,
        "action_history": [_event(0, "post_small_blind", 50),
                           _event(1, "post_big_blind", 100),
                           {**_event(0, "raise", 225), "is_timeout": True}]}})
    bot.on_match_end({})
    summary = bot.last_match_summary
    assert summary["version"] == "2.2.2"
    assert len(summary["table_sha256"]) == len(summary["range_spec_sha256"]) == 64
    assert summary["counters"]["range_applied"] == 1
    assert "hole_cards" not in str(summary)


def test_stale_limp_and_missing_blind_history_return_to_old_chart():
    limp = _spot("BB_VS_LIMP", 100)
    stale = replace(limp, pot=1100, to_call=900, min_raise=1900,
                    opponent_stacks=[9000])
    incomplete = replace(limp, action_history=limp.action_history[1:])
    for raw in (stale, incomplete):
        assert candidate_status(normalize(raw, 100)) == (None, "history_price_mismatch")
        action, old_action, trace, old_trace = _pair(raw)
        assert action.to_dict() == old_action.to_dict()
        assert trace["policy"] == old_trace["policy"]
        assert trace["preflop_range"]["status"] == "history_price_mismatch"


@pytest.mark.parametrize("context,depth,price", [
    ("BB_VS_OPEN", 100, 2), ("BB_VS_OPEN", 60, 2.5),
    ("BTN_FIRST_IN", 100, 0),
])
def test_existing_out_of_scope_nodes_remain_out_of_scope(context, depth, price):
    raw = _spot(context, depth, price)
    assert candidate_status(normalize(raw, 100)) == (None, "out_of_scope")
    action, old_action, trace, old_trace = _pair(raw)
    assert action.to_dict() == old_action.to_dict()
    assert trace["policy"] == old_trace["policy"]
    assert trace["preflop_range"]["status"] == "out_of_scope"
