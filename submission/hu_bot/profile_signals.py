"""Bounded observable river signals, separated from hidden-hand inference.

First-action rates have real opportunity denominators. Revealed hand strengths
are a selected subset, because a showdown often requires the hero to call.
Neither statistic is a calibrated bluff/value composition.
"""
from .poker import features


def first_river_key(event, dealer_seat, prior_hero_check=False):
    n = event.pf_raises_before
    pot_type = "LIMP" if n == 0 else "SRP" if n == 1 else "3BET" if n == 2 else "4BET"
    position = "IP" if event.seat == dealer_seat else "OOP"
    line = "after_check" if prior_hero_check else "open"
    return f"river:first:{pot_type}:{position}:{line}"


def first_river_action(model, event, hero_seat, dealer_seat, prior_hero_check=False):
    if (event.street != "river" or event.seat == hero_seat
            or event.street_raises_before != 0 or event.is_timeout
            or event.action not in {"check", "raise", "all_in"}):
        return None
    key = first_river_key(event, dealer_seat, prior_hero_check)
    aggression = event.action in {"raise", "all_in"} and event.amount_to > event.table_bet
    model.stat(key+":bet", .45).observe(aggression)
    model.stat(key+":overbet", .10).observe(aggression and event.size_ratio >= 1.25)
    return key if aggression and event.size_ratio >= 1.25 else None


def revealed_overbet_strength(model, key, hole, board):
    if key is None or len(hole) != 2 or len(board) != 5:
        return False
    cards = tuple(map(str, hole)) + tuple(map(str, board))
    if len(set(cards)) != 7:
        return False
    try:
        strength = features(tuple(map(str, hole)), tuple(map(str, board))).strength
    except (IndexError, KeyError, TypeError, ValueError):
        return False
    for label, observed in (("weak", strength < .50),
                            ("medium", .50 <= strength < .75),
                            ("strong", strength >= .75)):
        model.stat(key+":revealed_"+label, 1/3).observe(observed)
    return True
