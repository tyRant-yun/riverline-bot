from dataclasses import replace
from time import perf_counter

import pytest

from hu_bot.core import normalize
from hu_bot.memory import OpponentModel
from hu_bot.ranges import RangeState, posterior
from hu_bot.responses import response_keys
from hu_bot.value_optimizer import MIN_CONTEXT_OPPORTUNITIES, _score, plan_value
import hu_bot.value_optimizer as value_optimizer
from lab.scenarios import SRP, entry, spot


def fixture_state(hero="Ah Qd"):
    raw = spot(hero, "As 8c 4d 2h 9s",
               action_history=SRP+[entry(1, "check", 0, "river")])
    s = normalize(raw)
    public = posterior(s, s.hero_seat, perf_counter()+5)
    assert public.complete
    return s, public


def cards(*combos):
    # Explicitly normalized exact river range, with no Monte Carlo sampling.
    weight = 1/len(combos)
    return RangeState("fixture", {tuple(x.split()): weight for x in combos}, 1., 0)


def observed(actions, amount):
    """Record distinct completed hands through the production opportunity path."""
    model = OpponentModel()
    for hand, response in enumerate(actions):
        given = 0 if response == "fold" else amount if response == "call" else amount*3
        history = SRP+[entry(1, "check", 0, "river"),
                       entry(0, "raise", amount, "river"),
                       entry(1, response, given, "river")]
        model.observe_hand((amount, hand), {"action_history": history}, 0, 0)
    return model


def merge_models(*models):
    model = OpponentModel()
    for source in models:
        for key, stat in source.stats.items():
            if key not in model.stats:
                model.stats[key] = stat
            else:
                model.stats[key].success += stat.success
                model.stats[key].opportunities += stat.opportunities
    return model


def test_exact_payoffs_include_called_range_and_bet_fold_raise_loss():
    # Pot 100, bet 50: fold wins 100; win after call wins 150 net;
    # loss after call and raise each lose the full 50 bet.
    rows = [[.25, 1., 0., 1., 0.],
            [.25, 0., 1., 0., 0.],
            [.25, 0., 0., 0., 1.],
            [.25, 0., 0., 1., 0.]]
    score = _score(rows, 100, 50)
    assert score["called_equity"] == .5
    assert score["ev"] == pytest.approx(37.5)


def test_default_off_and_actual_context_opportunity_gate():
    s, public = fixture_state()
    villain = cards("Ks 9d", "Jd 8h", "Kd Qh")
    off, detail = plan_value(s, villain, public, observed(["call"]*100, 16),
                             perf_counter()+5)
    assert off is None and detail["reason"] == "disabled"
    sparse, detail = plan_value(s, villain, public,
                                observed(["call"]*(MIN_CONTEXT_OPPORTUNITIES-1), 16),
                                perf_counter()+5, enabled=True)
    assert sparse is None and detail["reason"] == "insufficient_context_response_evidence"
    # A populated generic cell cannot stand in for this pot/position/size cell.
    wrong = observed(["call"]*100, 16)
    other = replace(s, node=replace(s.node, pot_type="3BET"))
    policy, detail = plan_value(other, villain, public, wrong, perf_counter()+5,
                                enabled=True)
    assert policy is None and detail["reason"] == "insufficient_context_response_evidence"


def test_no_evidence_returns_before_evaluating_either_range(monkeypatch):
    s, public = fixture_state()
    villain = cards("Ks 9d")

    def forbidden(*_args, **_kwargs):
        raise AssertionError("range enumeration must be skipped")

    monkeypatch.setattr(value_optimizer, "evaluate", forbidden)
    policy, detail = plan_value(s, villain, public, OpponentModel(),
                                perf_counter()+5, enabled=True)
    assert policy is None
    assert detail["reason"] == "insufficient_context_response_evidence"


def test_call_wide_promotes_value_and_reports_identity_uncertainty():
    s, public = fixture_state()
    villain = cards("Ks 9d", "Jd 8h", "Kd Qh")
    model = observed(["call"]*100, 16)
    policy, detail = plan_value(s, villain, public, model, perf_counter()+5,
                                enabled=True)
    assert detail["complete"] and policy.weights == {"BET_SMALL": 1.}
    chosen = detail["candidates"][0]
    assert chosen["min_called_equity"] >= .5
    assert chosen["worst_ev"] > detail["check_ev"]+chosen["margin_required"]
    assert detail["aggregate_folds_do_not_identify_folded_hands"]
    assert detail["river_enumeration_complete"]
    assert chosen["evidence"]["key"] == response_keys("SRP", False, .32)[2]


def test_overfold_cannot_promote_weak_hand_and_check_superiority():
    s, public = fixture_state("7h 3d")
    villain = cards("Ks 9d", "Jd 8h", "Kd Qh")
    policy, detail = plan_value(s, villain, public, observed(["fold"]*90+["call"]*10, 16),
                                perf_counter()+5, enabled=True)
    assert policy.weights == {"CHECK": 1.}
    assert detail["candidates"] and all(not c["eligible"] for c in detail["candidates"])


def test_raise_risk_can_reverse_a_value_bet():
    s, public = fixture_state()
    villain = cards("Ks 9d", "Jd 8h", "Kd Qh")
    call_policy, call_detail = plan_value(s, villain, public, observed(["call"]*100, 16),
                                          perf_counter()+5, enabled=True)
    raise_policy, raise_detail = plan_value(s, villain, public,
                                            observed(["raise"]*80+["call"]*20, 16),
                                            perf_counter()+5, enabled=True)
    assert call_policy.weights == {"BET_SMALL": 1.}
    assert raise_policy.weights == {"CHECK": 1.}
    assert raise_detail["candidates"][0]["worst_ev"] < call_detail["candidates"][0]["worst_ev"]


def test_multiple_sizes_select_by_worst_case_ev():
    s, public = fixture_state()
    villain = cards("Ks 9d", "Jd 8h", "Kd Qh")
    model = merge_models(observed(["call"]*100, 16),
                         observed(["call"]*40+["raise"]*60, 33),
                         observed(["call"]*20+["raise"]*80, 50),
                         observed(["call"]*10+["raise"]*90, 63))
    policy, detail = plan_value(s, villain, public, model, perf_counter()+5, enabled=True)
    assert len(detail["candidates"]) == 4
    assert policy.weights == {"BET_SMALL": 1.}
    assert all(c["amount"] in {16, 33, 50, 62} for c in detail["candidates"])


def test_incomplete_invalid_blocked_and_deadline_use_atomic_fallback():
    s, public = fixture_state()
    model = observed(["call"]*100, 16)
    villain = cards("Ks 9d")
    assert plan_value(s, replace(villain, complete=False), public, model,
                      perf_counter()+5, enabled=True)[1]["reason"] == "incomplete_or_empty_range"
    blocked = cards("Ah 9d")
    assert plan_value(s, blocked, public, model, perf_counter()+5,
                      enabled=True)[1]["reason"] == "invalid_villain_range"
    policy, detail = plan_value(s, villain, public, model, perf_counter()-1,
                                enabled=True)
    assert policy is None and detail == {"complete": False, "reason": "deadline_atomic_discard"}
    assert plan_value(replace(s, in_position=False), villain, public, model,
                      perf_counter()+5, enabled=True)[1]["reason"] == "unsupported_node"


def test_full_990_combo_probe_under_decision_budget():
    s, public = fixture_state()
    villain = posterior(s, 1-s.hero_seat, perf_counter()+5)
    assert villain.complete and len(villain.weights) == 990
    model = merge_models(*(observed(["call"]*100, amount)
                           for amount in (16, 33, 50, 63)))
    start = perf_counter()
    policy, detail = plan_value(s, villain, public, model, start+.45, enabled=True)
    elapsed_ms = (perf_counter()-start)*1000
    assert detail["complete"], (detail, elapsed_ms)
    assert policy is not None and len(detail["candidates"]) == 4
    assert detail["river_enumeration_complete"]
