from dataclasses import FrozenInstanceError

from chipzen import GameState
from chipzen.models import Card

from hu_bot.accounting import (DecisionSnapshot, MatchEventDeduper, RuleSnapshot,
                               RawActionEvent, build_ledger, settlement_ledger)


def event(index, seat, action, amount, phase="preflop"):
    return RawActionEvent(index, seat, action, phase, amount, False,
                          (("seat", seat), ("action", action), ("amount", amount),
                           ("phase", phase)))


def test_raise_to_and_increment_are_separate_and_consistent():
    history = (event(0, 0, "post_small_blind", 5),
               event(1, 1, "post_big_blind", 10),
               event(2, 0, "raise", 25),
               event(3, 1, "call", 15))
    ledger = build_ledger(history, current_street="preflop", authoritative_pot=50,
                          authoritative_to_call=0, hero_seat=0,
                          hand_start_stacks=(100, 100), current_stacks=(75, 75),
                          seat_count=2)
    assert ledger.consistent
    assert ledger.committed_by_seat == (25, 25)
    assert ledger.contestable_pot == 50 and ledger.potential_uncalled == 0
    assert ledger.events[2].raise_to == 25 and ledger.events[2].incremental == 20
    assert ledger.events[3].amount_semantics == "call_increment"


def test_short_call_keeps_possible_return_outside_contestable_pot_until_settlement():
    history = (event(0, 0, "post_small_blind", 5),
               event(1, 1, "post_big_blind", 10),
               event(2, 0, "raise", 100),
               event(3, 1, "call", 40))
    ledger = build_ledger(history, current_street="preflop", authoritative_pot=150,
                          authoritative_to_call=0, hero_seat=0,
                          hand_start_stacks=(100, 50), current_stacks=(0, 0),
                          seat_count=2)
    assert ledger.committed_by_seat == (100, 50)
    assert ledger.contestable_pot == 100 and ledger.potential_uncalled == 50
    settled = settlement_ledger(ledger, {
        "payouts": [{"seat": 0, "amount": 100}, {"seat": 1, "amount": 0}],
        "uncalled_refunds": [50, 0],
    })
    assert settled.refunds == ((0, 50),)
    assert settled.payouts == ((0, 100), (1, 0))
    assert settled.consistent


def test_authority_conflict_is_recorded_without_rewriting_history():
    history = (event(0, 0, "post_small_blind", 5),
               event(1, 1, "post_big_blind", 10))
    ledger = build_ledger(history, current_street="preflop", authoritative_pot=115,
                          authoritative_to_call=20, hero_seat=0,
                          hand_start_stacks=(100, 100), current_stacks=(95, 90),
                          seat_count=2)
    assert not ledger.consistent
    assert "pot_history_gap:15->115" in ledger.conflicts
    assert "to_call_history_gap:5->20" in ledger.conflicts
    assert ledger.reconstructed_pot == 15


def test_decision_snapshot_is_deeply_immutable_and_keeps_authority_separate():
    state = GameState(hand_number=7, phase="preflop",
                      hole_cards=[Card.from_str("Ah"), Card.from_str("Kd")],
                      board=[], pot=15, your_stack=95, opponent_stacks=[90],
                      your_seat=0, dealer_seat=0, to_call=5, min_raise=20,
                      max_raise=95, valid_actions=["fold", "call", "raise"],
                      action_history=[{"seat": 0, "action": "post_small_blind", "amount": 5,
                                      "phase": "preflop", "is_timeout": False},
                                     {"seat": 1, "action": "post_big_blind", "amount": 10,
                                      "phase": "preflop", "is_timeout": False}])
    snapshot = DecisionSnapshot.from_game_state(
        state, rules=RuleSnapshot.from_config("m", {"big_blind": 10}),
        hand_start_stacks=(100, 100))
    assert snapshot.ledger.consistent
    assert snapshot.authority_pot == 15
    assert isinstance(snapshot.raw_events[0].raw, tuple)
    try:
        snapshot.authority_pot = 20
    except FrozenInstanceError:
        pass
    else:
        raise AssertionError("DecisionSnapshot must be immutable")


def test_match_deduper_survives_reconnect_and_700_hands_without_eviction():
    deduper = MatchEventDeduper("m")
    assert deduper.first_result("r1", 1)
    assert not deduper.first_result("r1", 1)
    assert not deduper.first_result("", 1)
    for hand in range(2, 702):
        assert deduper.first_result(f"r{hand}", hand)
        assert not deduper.first_result(f"r{hand}", hand)
    assert deduper.seen_count >= 701


def test_rule_snapshot_marks_schedule_unknown_until_received():
    snapshot = RuleSnapshot.from_config("m", {"variant": "nlhe", "big_blind": 10})
    assert snapshot.source == "platform_verified"
    assert snapshot.schedule_source == "unknown"
    observed = snapshot.with_observed_big_blind(20)
    assert observed.big_blind == 20 and observed.source == "platform_verified"


def test_symmetric_5bb_and_8bb_shoves_use_total_raise_to_and_no_refund():
    for bb, stack in ((100, 5 * 100), (100, 8 * 100)):
        blind = bb // 2
        history = (event(0, 0, "post_small_blind", blind),
                   event(1, 1, "post_big_blind", bb),
                   event(2, 0, "raise", stack),
                   event(3, 1, "call", stack-bb))
        ledger = build_ledger(history, current_street="preflop",
                              authoritative_pot=2 * stack, authoritative_to_call=0,
                              hero_seat=0, hand_start_stacks=(stack, stack),
                              current_stacks=(0, 0), seat_count=2)
        assert ledger.consistent
        assert ledger.committed_by_seat == (stack, stack)
        assert ledger.events[2].raise_to == stack
        assert ledger.events[2].incremental == stack-blind
        assert ledger.potential_uncalled == 0


def test_unequal_short_stack_call_keeps_overage_as_possible_refund():
    # BTN has 8BB, BB has 5BB.  The raise-to is still a total, while the
    # short BB contributes only the remaining 4BB after posting 1BB.
    history = (event(0, 0, "post_small_blind", 50),
               event(1, 1, "post_big_blind", 100),
               event(2, 0, "raise", 800),
               event(3, 1, "call", 400))
    ledger = build_ledger(history, current_street="preflop", authoritative_pot=1_300,
                          authoritative_to_call=0, hero_seat=0,
                          hand_start_stacks=(800, 500), current_stacks=(0, 0), seat_count=2)
    assert ledger.committed_by_seat == (800, 500)
    assert ledger.contestable_pot == 1_000
    assert ledger.potential_uncalled == 300
    settled = settlement_ledger(ledger, {"uncalled_refunds": [300, 0],
                                         "payouts": [{"seat": 0, "amount": 1000},
                                                     {"seat": 1, "amount": 0}]})
    assert settled.refunds == ((0, 300),)
    assert settled.contestable_pot == 1_000


def test_short_bb_call_at_5bb_and_8bb_is_incremental_not_raise_to():
    for stack in (5 * 100, 8 * 100):
        history = (event(0, 0, "post_small_blind", 50),
                   event(1, 1, "post_big_blind", 100),
                   event(2, 0, "raise", stack),
                   event(3, 1, "call", stack-100))
        ledger = build_ledger(history, current_street="preflop",
                              authoritative_pot=2 * stack, authoritative_to_call=0,
                              hero_seat=1, hand_start_stacks=(stack, stack),
                              current_stacks=(0, 0), seat_count=2)
        assert ledger.consistent
        assert ledger.events[-1].raise_to is None
        assert ledger.events[-1].incremental == stack-100


def test_blind_level_change_changes_bb_conversion_without_rewriting_chips():
    old = RuleSnapshot.from_config("m", {"big_blind": 100})
    new = old.with_observed_big_blind(200)
    assert old.chips_to_bb(200) == 2.0
    assert new.chips_to_bb(200) == 1.0
    assert old.big_blind == 100 and new.big_blind == 200


def test_decision_snapshot_rejects_invalid_authoritative_raise_bounds():
    state = GameState(hand_number=8, phase="preflop",
                      hole_cards=[Card.from_str("Ah"), Card.from_str("Kd")],
                      board=[], pot=150, your_stack=350, opponent_stacks=[400],
                      your_seat=0, dealer_seat=0, to_call=50, min_raise=500,
                      max_raise=400, valid_actions=["fold", "call", "raise"],
                      action_history=[{"seat": 0, "action": "post_small_blind", "amount": 50,
                                      "phase": "preflop", "is_timeout": False},
                                     {"seat": 1, "action": "post_big_blind", "amount": 100,
                                      "phase": "preflop", "is_timeout": False}])
    snapshot = DecisionSnapshot.from_game_state(state)
    assert not snapshot.ledger.consistent
    assert "authority:invalid_raise_bounds" in snapshot.conflicts
