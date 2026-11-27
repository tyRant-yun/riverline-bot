from collections import Counter

import pytest

from hu_bot.config import Config
from hu_bot.core import normalize
from hu_bot.engine import Engine
from hu_bot.preflop_blueprint import PreflopBlueprint, RuleSpec, small_tree_open_ev
from hu_bot.preflop_exploit import ActionValue, ExploitPlanner, research_values
from hu_bot.preflop_observation import (OpportunityLedger, OpponentPosterior,
                                         PreflopNodeKey, opportunities)
from hu_bot.strategy import preflop_policy
from lab.scenarios import BLINDS, entry, spot
from lab.hu100_matches import MatchRules
from lab.match import Baseline, play_hand
from hu_bot.poker import DECK
from bot import HUBot


def hand(first, second=None, third=None):
    history = BLINDS+[first]
    if second:
        history.append(second)
    if third:
        history.append(third)
    return history


def test_rule_spec_and_default_switch():
    assert RuleSpec().starting_stack_bb == 100 and RuleSpec().ante == 0
    assert RuleSpec().source == "assumed_for_research"
    assert Config().preflop_overfold_enabled is False


def test_three_node_classification_and_raw_size():
    open_ = hand(entry(0, "raise", 35), entry(1, "fold"))
    records, reason = opportunities(open_, 0, 10, 100)
    assert reason is None
    assert records[0][0].node == "BTN_FIRST_IN"
    assert records[1][0] == PreflopNodeKey("BB_VS_OPEN", 35, 10, "100bb", 0)
    assert records[1][1] == "fold"
    limp = hand(entry(0, "call", 5), entry(1, "raise", 40), entry(0, "fold"))
    records, reason = opportunities(limp, 0, 10, 100)
    assert reason is None
    assert [r[0].node for r in records] == ["BTN_FIRST_IN", "BB_VS_LIMP", "BTN_VS_ISO"]
    assert [r[1] for r in records] == ["limp", "raise", "fold"]
    three = hand(entry(0, "raise", 22), entry(1, "raise", 73), entry(0, "call", 51))
    records, reason = opportunities(three, 0, 10, 100)
    assert reason is None
    assert [r[0].node for r in records] == ["BTN_FIRST_IN", "BB_VS_OPEN", "BTN_VS_3BET"]


def test_ledger_exclusive_idempotent_and_skips_bad_hands():
    ledger = OpportunityLedger()
    h = hand(entry(0, "raise", 22), entry(1, "fold"))
    assert ledger.observe_hand("a", {"action_history": h}, 0, 0, 10, 100) == 1
    assert ledger.observe_hand("a", {"action_history": h}, 0, 0, 10, 100) == 0
    key = PreflopNodeKey("BB_VS_OPEN", 22, 10, "100bb", 0)
    assert ledger.counts[key] == Counter(fold=1)
    assert sum(ledger.counts[key].values()) == 1
    assert ledger.observe_hand("b", {"action_history": h}, 1, 0, 10, 100) == 1
    assert ledger.counts[PreflopNodeKey("BTN_FIRST_IN", 0, 10, "100bb", 0)]["raise"] == 1
    timeout = hand(entry(0, "raise", 22), dict(entry(1, "fold"), is_timeout=True))
    assert ledger.observe_hand("c", {"action_history": timeout}, 0, 0, 10, 100) == 0
    assert ledger.skipped["incomplete_or_timeout"] == 1
    assert ledger.observe_hand("d", {"action_history": BLINDS}, 0, 0, 10, 100) == 0
    assert ledger.skipped["incomplete_or_timeout"] == 2


def test_long_match_replayed_old_hand_stays_idempotent():
    history = hand(entry(0, "raise", 22), entry(1, "fold"))
    result = {"action_history": history}
    ledger = OpportunityLedger()
    model = Engine(Config(trace=False)).opponent
    for hand_id in range(601):
        assert ledger.observe_hand(hand_id, result, 0, 0, 10, 100) == 1
        model.observe_hand(hand_id, result, 0, 0, big_blind=10, depth_bb=100)
    before = ledger.summary()["opportunities"]
    observed = model.stats["fold_vs_open"].opportunities
    assert ledger.observe_hand(0, result, 0, 0, 10, 100) == 0
    model.observe_hand(0, result, 0, 0, big_blind=10, depth_bb=100)
    assert ledger.summary()["opportunities"] == before == 601
    assert model.stats["fold_vs_open"].opportunities == observed == 601
    assert len(model._seen) == len(model.preflop._seen) == 601


def test_ante_depth_and_size_isolation():
    ledger = OpportunityLedger()
    for index, (amount, bb, depth, ante) in enumerate(((22, 10, 100, 0),
                                                         (35, 10, 100, 0),
                                                         (22, 10, 30, 0),
                                                         (22, 10, 100, 2))):
        h = BLINDS + ([entry(0, "post_ante", 1), entry(1, "post_ante", 1)] if ante else [])
        h += [entry(0, "raise", amount), entry(1, "fold")]
        ledger.observe_hand(index, {"action_history": h}, 0, 0, bb, depth)
    assert len(ledger.counts) == 4
    assert ledger.counts[PreflopNodeKey("BB_VS_OPEN", 22, 10, "100bb", 0)] == Counter(fold=1)


def test_posterior_shrinkage_and_exact_interval():
    model = OpponentPosterior(20)
    prior = {"fold": .4, "call": .4, "raise": .2}
    empty = model.predict({}, prior)
    assert empty["mean"] == prior
    seen = model.predict(Counter(fold=8, call=2), prior)
    assert seen["n"] == 10 and seen["mean"]["fold"] == pytest.approx(16/30)
    assert sum(seen["mean"].values()) == pytest.approx(1)
    assert seen["interval_95"]["fold"][0] < seen["mean"]["fold"] < seen["interval_95"]["fold"][1]
    with pytest.raises(ValueError):
        model.predict(Counter(fold=1, banana=1), prior)


def test_size_collisions_are_merged_after_legalization():
    state = normalize(spot(your_seat=1, pot=20, your_stack=990,
                           opponent_stacks=[990], to_call=0, min_raise=20,
                           valid_actions=["check", "raise"],
                           action_history=BLINDS+[entry(0, "call", 5)]), 10)
    base = preflop_policy(state)
    executable = PreflopBlueprint().legal_policy(state)
    assert len([key for key in executable if key[0] == "raise"]) <= 1
    assert sum(executable.values()) == pytest.approx(1)
    assert executable.get(("raise", 35), 0) == pytest.approx(
        base.weights.get("RAISE_SMALL", 0)+base.weights.get("RAISE_MEDIUM", 0))


def test_research_tree_has_normal_and_reverse_counterexample():
    assert small_tree_open_ev(.85) > 0
    assert small_tree_open_ev(.40) < 0
    assert small_tree_open_ev(.10) < small_tree_open_ev(.40)
    assert all(not v.validated for v in research_values(.4))


def test_unvalidated_candidate_returns_identical_policy_and_rng_action():
    state = normalize(spot("7h 2d"), 10)
    base = preflop_policy(state)
    ledger = OpportunityLedger()
    key = PreflopNodeKey("BB_VS_OPEN", 22, 10, "100bb", 0)
    ledger.counts[key] = Counter(fold=100)
    planner = ExploitPlanner(research_values(.4))
    policy, reason = planner.propose(base, state, ledger, enabled=True)
    assert policy.weights == base.normalized().weights
    assert reason["reason"] == "no_validated_combo_value"
    a = Engine(Config(trace=False)).evaluate(spot("7h 2d"))
    b = Engine(Config(trace=False, preflop_overfold_enabled=True)).evaluate(spot("7h 2d"))
    assert a == b


def test_test_only_validated_value_changes_only_eligible_combo_and_is_capped():
    ledger = OpportunityLedger()
    ledger.counts[PreflopNodeKey("BB_VS_OPEN", 22, 10, "100bb", 0)] = Counter(fold=100)
    planner = ExploitPlanner([ActionValue("72o", 2.2, .5, .1, .2, validated=True,
                                           source="test_only")])
    state = normalize(spot("7h 2d"), 10)
    base = preflop_policy(state)
    final, reason = planner.propose(base, state, ledger, enabled=True)
    assert reason["applied"] and 0 < reason["weight"] <= .15
    assert final.weights["RAISE_SMALL"] > base.weights["RAISE_SMALL"]
    other = normalize(spot("Ah Ad"), 10)
    unchanged, reason = planner.propose(preflop_policy(other), other, ledger, enabled=True)
    assert not reason["applied"] and unchanged.weights == preflop_policy(other).weights


def test_continuous_stack_hand_conserves_chips_and_schedule_is_explicit():
    players = [Baseline("call", 1), Baseline("call", 2)]
    net, _, result = play_hand(players, DECK, starting_stacks=[600, 1400], bb=10)
    assert sum(result["stacks"]) == 2000 and sum(net) == 0
    assert MatchRules(ramp_every_hands=None).blind(100) == 10
    assert MatchRules(ramp_every_hands=25).blind(25) == 20


def test_round_start_uses_authoritative_start_stacks_even_without_hero_turn():
    bot = HUBot()
    bot.on_round_start({"round_id": "h10", "state": {"hand_number": 10,
                        "dealer_seat": 0, "big_blind": 10, "stacks": [1000, 1000]}})
    assert bot.engine.hand_depths[10] == 100
    bot.hero_seat = 1
    bot.on_round_result({"round_id": "h10", "result": {"action_history":
                         hand(entry(0, "fold"))}})
    key = PreflopNodeKey("BTN_FIRST_IN", 0, 10, "100bb", 0)
    assert bot.engine.opponent.preflop.counts[key] == Counter(fold=1)
