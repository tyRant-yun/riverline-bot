from time import perf_counter
from types import SimpleNamespace

from hu_bot.core import Policy, normalize
from hu_bot.facing_river import guard_unpriced_raise
from lab.scenarios import SRP, entry, spot


def fixture(**changes):
    raw = spot("Ah Qd", "As 8c 4d 2h 9s", pot=250, to_call=200,
               valid_actions=["fold", "call", "raise"],
               action_history=SRP+[entry(1, "raise", 200, "river")],
               min_raise=400, max_raise=975)
    values = dict(state=normalize(raw), base_policy=Policy({"RAISE_MEDIUM": .8, "CALL": .2}, "value_raise"),
                  villain_range=SimpleNamespace(complete=True, weights={("Kd", "Kh"): 1.}),
                  equity_result=SimpleNamespace(equity=.8, exact=True, nuts=False),
                  deadline=perf_counter()+1, enabled=True)
    values.update(changes)
    return guard_unpriced_raise(**values)


def test_positive_call_converts_only_threshold_raise_mass():
    policy, detail = fixture()
    assert policy.weights == {"CALL": 1.}
    assert detail["raise_mass_to_call"] == .8 and detail["applied"]


def test_default_off_and_non_threshold_policy_preserve_base():
    assert fixture(enabled=False)[0] is None
    assert fixture(base_policy=Policy({"RAISE_MEDIUM": 1}, "other"))[0] is None


def test_nuts_and_inexact_equity_preserve_base():
    assert fixture(equity_result=SimpleNamespace(equity=.8, exact=True, nuts=True))[0] is None
    assert fixture(equity_result=SimpleNamespace(equity=.8, exact=False, nuts=False))[0] is None


def test_nonpositive_call_does_not_create_a_new_fold_rule():
    policy, detail = fixture(equity_result=SimpleNamespace(equity=.2, exact=True, nuts=False))
    assert policy is None and detail["reason"] == "call_not_proven_positive"


def test_mismatched_history_and_short_call_return_no_override():
    raw = spot("Ah Qd", "As 8c 4d 2h 9s", pot=250, to_call=200,
               valid_actions=["fold", "call", "raise"],
               action_history=SRP+[entry(1, "raise", 100, "river")])
    assert fixture(state=normalize(raw))[0] is None
    raw.your_stack = 100
    assert fixture(state=normalize(raw))[0] is None


def test_deadline_atomic_fallback():
    assert fixture(deadline=perf_counter()-1)[0] is None
