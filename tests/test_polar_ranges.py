from time import perf_counter

import pytest

from hu_bot import polar
from hu_bot.core import normalize
from hu_bot.poker import evaluate
from hu_bot.ranges import posterior
from lab.scenarios import SRP, entry, spot


BOARD = "As 8c 4d 2h 9s"


def overbet(ratio=2, tail=()):
    bet = 50 * ratio
    history = SRP + [entry(1, "raise", bet, "river")] + list(tail)
    return normalize(spot("Ah 7d", BOARD, pot=50 + bet, to_call=bet,
        your_stack=975, opponent_stacks=[975-bet], your_seat=0, dealer_seat=0,
        valid_actions=["fold", "call", "raise"], action_history=history))


def get(state, enabled=True):
    return posterior(state, 1, perf_counter() + 5, polar_river=enabled)


def test_flag_off_is_identical_to_legacy_default():
    state = overbet(2)
    a = posterior(state, 1, perf_counter() + 5)
    b = posterior(state, 1, perf_counter() + 5, polar_river=False)
    assert a.weights == b.weights and a.last_update == b.last_update
    assert a.river_components is b.river_components is None


@pytest.mark.parametrize("ratio", [2, 4, 8])
def test_2p_4p_8p_are_normalized_legal_and_positive(ratio):
    state = overbet(ratio)
    result = get(state)
    components = result.river_components
    legal = set(result.weights)
    assert result.complete and components is not None
    assert components["ratio"] == ratio and components["calibrated"] is False
    assert set(components) == {"value_weights", "bluff_weights", "value_stress_weights",
                               "value_concentration", "bluff_share", "bluff_interval",
                               "ratio", "model", "calibrated"}
    assert set(components["value_weights"]) == set(components["bluff_weights"]) == legal
    assert len(legal) == 990
    assert all(not set(combo) & set(state.hero_cards + state.board) for combo in legal)
    for weights in (result.weights, components["value_weights"], components["bluff_weights"],
                    *components["value_stress_weights"].values()):
        assert sum(weights.values()) == pytest.approx(1)
        assert min(weights.values()) > 0


def test_size_beyond_2p_changes_share_components_and_posterior_continuously():
    results = [get(overbet(ratio)) for ratio in (2, 4, 8)]
    shares = [r.river_components["bluff_share"] for r in results]
    assert shares == sorted(shares) and len(set(shares)) == 3
    combo = ("8d", "8h")
    values = [r.river_components["value_weights"][combo] for r in results]
    posteriors = [r.weights[combo] for r in results]
    assert len(set(values)) == len(set(posteriors)) == 3
    # Nearby off-grid sizes do not collapse into a bucket.
    a, b = get(overbet(2.01)), get(overbet(2.02))
    assert a.river_components["bluff_share"] < b.river_components["bluff_share"]
    assert a.weights != b.weights


@pytest.mark.parametrize("q,expected", [(.1, {"v": .9, "b": .1}),
                                         (.5, {"v": .5, "b": .5}),
                                         (.9, {"v": .1, "b": .9})])
def test_known_value_balanced_bluff_heavy_distribution_oracles(q, expected):
    value = {"v": 1., "b": 0.}
    bluff = {"v": 0., "b": 1.}
    assert polar.mix_components(value, bluff, q) == pytest.approx(expected)


def test_summary_never_emits_full_component_maps():
    result = get(overbet(2))
    summary = result.summary()["river_components"]
    assert summary["value_combos"] == summary["bluff_combos"] == 990
    assert "value_weights" not in summary and "bluff_weights" not in summary


@pytest.mark.parametrize("board,weak_combo,max_mass", [
    ("As 8c 4d 2h 9s", ("Ac", "Kd"), .20),       # dry ace-high: top pair
    ("As Ad 8c 4d 2h", ("Kc", "Qd"), .10),       # paired: board pair / kickers
    ("As 8s 4s 2h 9d", ("Ac", "Kd"), .20),       # monotone: non-flush top pair
])
def test_public_weak_showdown_region_does_not_dominate_value(board, weak_combo, max_mass):
    bet = 100
    state = normalize(spot("Jh Td", board, pot=150, to_call=bet,
        your_stack=975, opponent_stacks=[875], your_seat=0, dealer_seat=0,
        valid_actions=["fold", "call", "raise"],
        action_history=SRP+[entry(1, "raise", bet, "river")]))
    components = get(state).river_components
    cutoff = evaluate(weak_combo + tuple(board.split()))
    masses = {}
    for name, weights in {"central": components["value_weights"],
                          **components["value_stress_weights"]}.items():
        masses[name] = sum(mass for combo, mass in weights.items()
                           if evaluate(combo + state.board) <= cutoff)
    assert masses["tight"] < masses["central"] < masses["wide"]
    assert masses["central"] < max_mass


def test_only_current_last_first_aggression_keeps_components():
    state = overbet(2, tail=[entry(0, "call", 100, "river")])
    assert get(state).river_components is None
    raised = normalize(spot("Ah 7d", BOARD, pot=350, to_call=200,
        action_history=SRP+[entry(0, "raise", 50, "river"), entry(1, "raise", 250, "river")],
        valid_actions=["fold", "call", "raise"]))
    assert get(raised).river_components is None
    timeout = overbet(2)
    timeout.action_history[-1]["is_timeout"] = True
    assert get(timeout).river_components is None


def test_threshold_is_1_25_pot_and_non_aggression_does_not_trigger():
    assert get(overbet(1.24)).river_components is None
    # Chip amounts are integral: 63 into 50 is the first representable size above 1.25.
    assert get(overbet(1.26)).river_components is not None
    state = normalize(spot("Ah 7d", BOARD, pot=112, to_call=0,
        action_history=SRP+[entry(1, "call", 62, "river")]))
    assert get(state).river_components is None


def test_timeout_is_atomic_and_keeps_full_pre_event_support(monkeypatch):
    state = overbet(2)
    before = posterior(state, 1, perf_counter() + 5, max_updates=1)
    monkeypatch.setattr(polar, "build", lambda *args, **kwargs: (_ for _ in ()).throw(TimeoutError()))
    result = get(state)
    assert not result.complete and result.river_components is None
    assert result.weights == before.weights
    assert len(result.weights) == 990 and min(result.weights.values()) > 0


def test_sparse_or_unseparable_support_is_rejected_before_publication():
    deadline = perf_counter() + 5
    with pytest.raises(ValueError, match="nonempty prior"):
        polar.build({("2c", "3d"): 1.}, tuple(BOARD.split()), 2, deadline)


def test_hero_public_range_never_receives_opponent_polar_components():
    state = overbet(2)
    hero = posterior(state, state.hero_seat, perf_counter() + 5, polar_river=True)
    assert hero.river_components is None
