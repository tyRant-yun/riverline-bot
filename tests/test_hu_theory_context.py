from hu_bot.core import Policy, normalize
from hu_bot.history import ledger
from hu_bot.memory import OpponentModel
from hu_bot.poker import features
from hu_bot.responses import response_keys
from hu_bot.strategy import exploit
from lab.scenarios import BLINDS, entry, spot


def observe(history, key=1, hero=0, dealer=0):
    model = OpponentModel()
    model.observe_hand(key, {"action_history": history}, hero, dealer)
    return model


def test_first_bet_fold_and_checkraise_fold_use_separate_opportunities():
    first_bet = BLINDS + [entry(0, "raise", 25), entry(1, "call", 15),
                          entry(0, "raise", 50, "flop"), entry(1, "fold", 0, "flop")]
    model = observe(first_bet)
    assert model.stats["fold_to_flop_bet"].opportunities == 1
    assert "fold_to_flop_raise" not in model.stats

    checkraise = BLINDS + [entry(0, "raise", 25), entry(1, "call", 15),
                           entry(1, "raise", 50, "flop"),
                           entry(0, "raise", 150, "flop"),
                           entry(1, "fold", 0, "flop")]
    model = observe(checkraise)
    assert "fold_to_flop_bet" not in model.stats
    assert model.stats["fold_to_flop_raise"].opportunities == 1


def test_btn_isolation_does_not_reuse_fold_to_3bet_stat():
    model = OpponentModel()
    stat = model.stat("fold_to_3bet", .45)
    stat.success, stat.opportunities = 200, 200
    bb_open = normalize(spot("Ah Qh", your_seat=1, dealer_seat=0,
                             pot=30, your_stack=990, opponent_stacks=[980], to_call=10,
                             action_history=BLINDS + [entry(0, "raise", 20)]))
    btn_isolation = normalize(spot("Ah Qh", your_seat=0, dealer_seat=0,
                                   pot=30, your_stack=990, opponent_stacks=[980], to_call=10,
                                   action_history=BLINDS + [entry(1, "raise", 20)]))
    base = Policy({"CALL": 1})
    bb_final, bb_detail = exploit(base, bb_open, features(bb_open.hero_cards, bb_open.board), model, .15)
    iso_final, iso_detail = exploit(base, btn_isolation,
                                    features(btn_isolation.hero_cards, btn_isolation.board), model, .15)
    assert bb_detail["weight"] > 0 and bb_final.weights != base.weights
    assert iso_detail["weight"] == 0 and iso_final.weights == base.weights


def test_all_in_call_does_not_count_as_raise_but_all_in_raise_does():
    call_history = BLINDS + [entry(0, "all_in", 10)]
    call_model = observe(call_history, hero=1, dealer=0)
    assert call_model.stats["btn_open"].success == 0
    assert "three_bet" not in call_model.stats

    raise_history = BLINDS + [entry(0, "all_in", 100)]
    raise_model = observe(raise_history, hero=1, dealer=0)
    assert raise_model.stats["btn_open"].success == 1


def test_river_all_in_call_and_raise_have_distinct_response_counts():
    call_history = BLINDS + [entry(0, "raise", 25), entry(1, "call", 15),
                             entry(0, "raise", 50, "river"), entry(1, "all_in", 50, "river")]
    raise_history = BLINDS + [entry(0, "raise", 25), entry(1, "call", 15),
                              entry(0, "raise", 50, "river"), entry(1, "all_in", 150, "river")]
    call_model = observe(call_history, hero=0, dealer=0)
    raise_model = observe(raise_history, hero=0, dealer=0)
    key = response_keys("SRP", False, 50 / 50)[0]
    assert call_model.stats[key + ":raise"].success == 0
    assert raise_model.stats[key + ":raise"].success == 1


def test_ledger_marks_all_in_call_without_street_raise():
    event = ledger(BLINDS + [entry(0, "all_in", 10)])[2]
    assert event.amount_to == event.table_bet
    assert event.street_raises_before == 0


def test_candidate_keeps_other_statistics_unchanged():
    history = BLINDS + [entry(0, "raise", 25), entry(1, "call", 15),
                        entry(1, "raise", 50, "flop"), entry(0, "fold", 0, "flop")]
    model = observe(history)
    assert model.stats["flop_bet"].opportunities == 1
    assert model.stats["flop_bet"].success == 1
