"""Experimental size-conditioned river polarization (uncalibrated prior).

The model uses only public board/range information.  It never labels a combo
as a bluff by comparing it with hero's private hand.
"""
from math import log2
from time import perf_counter

from .poker import evaluate


FLOOR = 1e-6
SENSITIVITY_HALF_WIDTH = .12  # Predeclared model stress band, not a confidence interval.
VALUE_STRESS = {
    # Public-rank concentration alternatives.  These are model stress variants,
    # not calibrated credible bounds and not labels relative to hero's hand.
    "wide": (.84, .025, 22., 3.),
    "central": (.88, .020, 26., 3.),
    "tight": (.92, .015, 32., 4.),
}


def _sigmoid(x):
    if x >= 30:
        return 1.0
    if x <= -30:
        return 0.0
    from math import exp
    return 1 / (1 + exp(-x))


def normalize(weights):
    total = sum(weights.values())
    if total <= 0:
        raise ValueError("polar component has no support")
    return {combo: weight / total for combo, weight in weights.items()}


def mix_components(value_weights, bluff_weights, bluff_share):
    """Mix separately normalized components while preserving positive support."""
    if not 0 <= bluff_share <= 1 or set(value_weights) != set(bluff_weights):
        raise ValueError("invalid polar mixture")
    return normalize({combo: (1 - bluff_share) * value_weights[combo]
                      + bluff_share * bluff_weights[combo]
                      for combo in value_weights})


def build(prior, board, ratio, deadline):
    """Build a complete update or raise TimeoutError without publishing a prefix."""
    if len(board) != 5 or ratio < 1.25 or len(prior) < 8:
        raise ValueError("polar model requires a river overbet and nonempty prior")
    ranked = []
    for index, (combo, mass) in enumerate(prior.items()):
        if index % 32 == 0 and perf_counter() >= deadline:
            raise TimeoutError
        ranked.append((evaluate(combo + tuple(board)), combo, mass))
    ranked.sort(key=lambda row: row[0])
    total = sum(mass for _, _, mass in ranked)
    if total <= 0:
        raise ValueError("empty prior mass")

    # Midrank percentile is public relative hand strength under the pre-bet range.
    by_rank = {}
    for rank, _, mass in ranked:
        by_rank[rank] = by_rank.get(rank, 0.0) + mass
    rank_keys = sorted(by_rank)
    if len(rank_keys) < 2:
        raise ValueError("polar components cannot be separated")
    cumulative, running = {}, 0.0
    for rank in rank_keys:
        cumulative[rank] = (running + .5 * by_rank[rank]) / total
        running += by_rank[rank]

    pressure = max(0.0, log2(ratio / 1.25))
    value_raw = {name: {} for name in VALUE_STRESS}
    bluff_raw = {}
    for index, (rank, combo, mass) in enumerate(ranked):
        if index % 32 == 0 and perf_counter() >= deadline:
            raise TimeoutError
        percentile = cumulative[rank]
        # Even the wide stress case represents a polarized *value* tail rather
        # than the old broad made-hand region. Larger sizes smoothly concentrate it.
        for name, (base_cut, cut_rate, base_slope, slope_rate) in VALUE_STRESS.items():
            value_cut = min(.985, base_cut + cut_rate * pressure)
            value_slope = base_slope + slope_rate * pressure
            value_density = FLOOR + _sigmoid((percentile - value_cut) * value_slope)
            value_raw[name][combo] = mass * value_density
        # The bluff component is separately normalized.  It has its own public
        # low-strength density, so increasing value selectivity cannot make a
        # fixed additive bluff floor dominate the posterior.
        bluff_density = FLOOR + (1 - percentile) ** (1.35 + .18 * pressure)
        bluff_raw[combo] = mass * bluff_density
    values = {name: normalize(raw) for name, raw in value_raw.items()}
    value = values["central"]
    bluff = normalize(bluff_raw)

    # Toy indifference prior for a polarized bet B into P0: q=B/(P0+2B).
    # It is a baseline sensitivity input, not an estimate of this opponent.
    q = ratio / (1 + 2 * ratio)
    lo = max(0.0, q - SENSITIVITY_HALF_WIDTH)
    hi = min(1.0, q + SENSITIVITY_HALF_WIDTH)
    mixed = mix_components(value, bluff, q)
    concentration = {name: {"percentile_cut": min(.985, spec[0] + spec[1] * pressure),
                            "slope": spec[2] + spec[3] * pressure}
                     for name, spec in VALUE_STRESS.items()}
    return mixed, {"value_weights": value, "bluff_weights": bluff,
                   "value_stress_weights": {name: weights for name, weights in values.items()
                                            if name != "central"},
                   "value_concentration": concentration,
                   "bluff_share": q, "bluff_interval": [lo, hi],
                   "ratio": ratio, "model": "public-percentile-polar-toy-v2",
                   "calibrated": False}
