from math import isfinite
from time import perf_counter

from hu_bot.config import Config
from hu_bot.core import normalize
from hu_bot.engine import Engine
from hu_bot.memory import OpponentModel
from hu_bot.opponent_actions import ActionCell
from hu_bot.ranges import _calibrated_likelihoods, posterior
from lab.scenarios import SRP, entry, spot


def state():
    history = SRP + [entry(1, "raise", 50, "river")]
    return normalize(spot("Ah 7d", "As 8c 4d 2h 9s", action_history=history,
                          pot=100, to_call=50, opponent_stacks=[925],
                          valid_actions=["fold", "call", "raise"]))


def model(bets):
    result = OpponentModel()
    result.action_cells[("river", "SRP", "BB", "unopened", None)] = ActionCell(
        {"bet": bets, "check": 48-bets})
    return result


def run(s, m, enabled):
    return posterior(s, 1, perf_counter()+5, opponent_model=m,
                     action_likelihood_calibration=enabled)


def test_default_off_and_explicit_off_match_each_action():
    assert Config().action_likelihood_calibration is False
    s, m = state(), model(48)
    implicit = posterior(s, 1, perf_counter()+5, opponent_model=m)
    explicit = run(s, m, False)
    assert implicit.weights == explicit.weights
    assert implicit.summary() == explicit.summary()
    assert "calibration" not in implicit.summary()


def test_missing_or_sparse_exact_cell_keeps_old_range_even_with_broad_stats():
    s, m = state(), OpponentModel()
    for _ in range(40):
        m.stat("river_bet", .45).observe(True)
    old = run(s, m, False)
    missing = run(s, m, True)
    assert missing.weights == old.weights
    assert missing.calibration["reasons"] == {"no_exact_cell": 1}
    m.action_cells[("river", "SRP", "BB", "unopened", None)] = ActionCell(
        {"bet": 23, "check": 0})
    sparse = run(s, m, True)
    assert sparse.weights == old.weights
    assert sparse.calibration["reasons"] == {"sparse_cell": 1}


def test_exact_cell_broadens_aggression_conservatively_without_broad_adjustment():
    s, m = state(), model(48)
    old = run(s, m, False)
    calibrated = run(s, m, True)
    weak, strong = ("3c", "6h"), ("8d", "8h")
    assert calibrated.complete and calibrated.calibration["applied"] == 1
    assert calibrated.weights[weak]/calibrated.weights[strong] > old.weights[weak]/old.weights[strong]
    assert all(isfinite(p) and p > 0 for p in calibrated.weights.values())
    assert abs(sum(calibrated.weights.values())-1) < 1e-12
    # The old broad statistic is ignored for the same calibrated action event.
    for _ in range(50):
        m.stat("river_bet", .45).observe(True)
    assert run(s, m, True).weights == calibrated.weights


def test_deadline_and_invalid_likelihood_abort_atomically(monkeypatch):
    from hu_bot import ranges

    s, m = state(), model(48)
    prior = posterior(s, 1, perf_counter()+5, max_updates=1)
    monkeypatch.setattr(ranges, "_calibrated_likelihoods", lambda *args: None)
    aborted = run(s, m, True)
    assert not aborted.complete
    assert aborted.weights == prior.weights
    assert aborted.calibration["applied"] == 0
    assert aborted.calibration["reasons"] == {"unsafe_or_deadline": 1}
    assert _calibrated_likelihoods({"x": 1.}, {"x": float("nan")},
                                   ActionCell({"bet": 24}), "bet", perf_counter()+5) is None


def test_engine_trace_has_bounded_calibration_counts_when_enabled():
    engine = Engine(Config(trace=False, action_likelihood_calibration=True))
    engine.opponent = model(48)
    raw = spot("Ah 7d", "As 8c 4d 2h 9s", action_history=SRP +
               [entry(1, "raise", 50, "river")], pot=100, to_call=50,
               opponent_stacks=[925], valid_actions=["fold", "call", "raise"])
    engine.evaluate(raw)
    trace = engine.last_trace["villain_range"]["calibration"]
    assert trace["applied"] == 1
    assert set(trace) == {"enabled", "applied", "reasons"}
