"""Finite, hierarchical first-river-bet response counters (not showdown labels)."""
from math import sqrt


def size_bucket(fraction):
    return "small" if fraction <= .4 else "medium" if fraction <= .8 else "pot" if fraction <= 1.1 else "over"


def response_keys(pot_type, defender_ip, fraction):
    bucket = size_bucket(fraction)
    return ("response:river", f"response:river:{bucket}",
            f"response:river:{pot_type}:{'IP' if defender_ip else 'OOP'}:{bucket}")


def observe_response(model, event, hero_seat, dealer_seat):
    # Only the first response to hero's opening river bet. Never pool bet/raise responses.
    if (event.street != "river" or event.seat == hero_seat or event.is_timeout
            or event.street_raises_before != 1 or event.previous_aggressor != hero_seat
            or event.to_call <= 0 or event.action not in {"fold", "call", "raise", "all_in"}):
        return
    is_raise = event.action == "raise" or (
        event.action == "all_in" and event.amount_to > event.table_bet)
    n = event.pf_raises_before
    pot_type = "LIMP" if n == 0 else "SRP" if n == 1 else "3BET" if n == 2 else "4BET"
    # Reconstruct price BEFORE the defender's action, including for folds and raises.
    fraction = event.to_call / max(1, event.pot_before-event.to_call)
    for key in response_keys(pot_type, event.seat == dealer_seat, fraction):
        model.stat(key+":fold", .4).observe(event.action == "fold")
        model.stat(key+":raise", .1).observe(is_raise)


def evidence(model, pot_type, defender_ip, fraction):
    """Prefer a contextual cell; use broader, more conservative estimates when sparse."""
    keys = response_keys(pot_type, defender_ip, fraction)
    for key, minimum, cap in ((keys[2], 12, .9), (keys[1], 24, .6), (keys[0], 48, .3)):
        fold, raise_ = model.stats.get(key+":fold"), model.stats.get(key+":raise")
        if fold is None or raise_ is None or fold.opportunities < minimum:
            continue
        n = fold.opportunities
        if n != raise_.opportunities:
            continue
        return {"key": key, "n": n, "fold": fold.mean, "raise": raise_.mean,
                "weight": min(cap, n/(n+40)),
                # Conservative engineering gate, not an exact Beta credible interval.
                "fold_guard": max(0., fold.mean-2*sqrt(fold.mean*(1-fold.mean)/(n+21)))}
    return None
