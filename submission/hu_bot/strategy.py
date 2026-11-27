"""Replaceable baseline policy, bounded exploit, sizing, and legality layers."""
from chipzen import Action

from .core import Policy
from .preflop import preflop_policy as preflop_policy


def opponent_led_three_streets(s):
    """True when villain made the first aggressive action on each postflop street.

    A raise after our bet (including a check-raise) is a different line from
    betting into us. History is chronological; only completed prior streets
    and the current street before our decision are considered.
    """
    first_aggressor = {}
    for event in s.action_history:
        street = event.get("phase")
        if (street in {"flop", "turn", "river"}
                and event.get("action") == "raise"
                and street not in first_aggressor):
            first_aggressor[street] = event.get("seat")
    return all(first_aggressor.get(street) == 1 - s.hero_seat
               for street in ("flop", "turn", "river"))


def postflop_policy(s, f, eq):
    e = eq.equity
    reliable = eq.exact or eq.samples >= 64
    if not reliable:
        e = f.strength*.85
    odds = s.to_call/max(1, s.pot+s.to_call)
    draw = any(d in f.draws for d in ("FLUSH_DRAW", "OESD", "COMBO_DRAW"))
    # Realization haircut: a checkdown-equity estimate is not a solved future-street EV.
    realization = 1 if s.street == "river" else .95 if s.in_position else .86
    adjusted = max(0, e*realization-(0 if eq.exact else min(.06, eq.stderr)))
    strong = f.made in {"STRONG", "VERY_STRONG"}
    if s.to_call:
        if eq.nuts and not f.board_only:
            return Policy({"ALL_IN" if s.spr <= 3 else "RAISE_LARGE": 1}, "nuts_value_raise")
        # Repeated raises on a paired board can concentrate villain on trips.
        # Once villain is all-in, however, a near-free call has no future-street
        # reverse implied odds. Do not fold known positive equity for 1 chip
        # into a huge pot merely because the raise count is high.
        paired_two_pair = (getattr(f, "category", None) == 2
                           and "PAIRED" in getattr(f, "texture", ()))
        if paired_two_pair and s.node.street_raises >= 3 and not draw:
            # Calling an all-in fully realizes showdown equity. With sampled
            # equity, demand a two-standard-error lower bound above the price.
            allin_lower_equity = e - (0 if eq.exact else 2*eq.stderr)
            if (s.villain_stack == 0 and odds <= .02 and reliable
                    and allin_lower_equity > odds):
                return Policy({"CALL": 1}, "paired_board_priced_allin_call")
            return Policy({"FOLD": 1}, "paired_board_multi_raise_fold")
        # The range model has a fixed bluff floor. Suppress low-sample calls on
        # the costly direct-barrel line. For reliable equity, retain the full
        # safety cushion on large commitments. At lower commitment, replace
        # the blanket fold with a capped bluff-catch mix while the estimated
        # edge is small: broad-range exact enumeration is not range truth.
        if s.street == "river" and paired_two_pair and not f.wet and odds >= .18:
            commitment = s.to_call/max(1, s.hero_stack)
            if opponent_led_three_streets(s):
                margin = adjusted - odds
                if not reliable or margin <= 0 or commitment >= .5 and margin < .18:
                    return Policy({"FOLD": 1}, "paired_board_triple_barrel_fold")
                if margin < .18:
                    call = min(.30, .40*margin/.18)
                    return Policy({"CALL": call, "FOLD": 1-call},
                                  "paired_board_cautious_bluffcatch")
        if strong and e >= .77 and not paired_two_pair:
            return Policy({"ALL_IN": .85, "CALL": .15} if s.spr <= 1.5 else
                          {"RAISE_MEDIUM": .8, "CALL": .2}, "value_raise")
        margin = adjusted-odds
        if f.made in {"MEDIUM", "WEAK_SHOWDOWN"}:
            # Large commitments with marginal made hands need stronger range evidence.
            commitment = s.to_call/max(1, s.hero_stack)
            margin -= .10 if commitment > .4 and s.spr > 1.5 else 0
            margin -= .05*max(0, s.node.street_raises-1)
        if margin < -.03:
            return Policy({"FOLD": 1}, "insufficient_realized_equity")
        if draw and s.street != "river" and s.node.street_raises <= 1 and e >= .43:
            return Policy({"CALL": .80, "RAISE_SMALL": .12, "FOLD": .08}, "selective_semibluff")
        if margin >= .10:
            return Policy({"CALL": 1}, "profitable_defend")
        call = min(.95, max(.10, .55+margin*4))
        return Policy({"CALL": call, "FOLD": 1-call}, "marginal_defend")
    if eq.nuts and not f.board_only:
        return Policy({"BET_LARGE": 1}, "nuts_value")
    if f.board_only:
        return Policy({"CHECK": 1}, "board_plays")
    if strong and e >= .70:
        if s.spr <= 1.2 and e >= .80:
            return Policy({"ALL_IN": 1}, "low_spr_value")
        return Policy({"BET_LARGE": .75, "BET_MEDIUM": .25} if e >= .85 else
                      {"BET_MEDIUM": .85, "CHECK": .15}, "value_capture")
    if s.street == "river":
        if e >= .64 and f.made in {"STRONG", "MEDIUM"}:
            return Policy({"BET_SMALL": .65, "CHECK": .35}, "thin_value")
        if f.made == "AIR" and f.blocker and s.node.street_raises == 0:
            return Policy({"BET_MEDIUM": .12, "CHECK": .88}, "river_blocker_bluff")
        return Policy({"CHECK": 1}, "river_showdown_or_giveup")
    if f.made == "MEDIUM":
        return Policy({"BET_SMALL": .20 if not f.wet else .10, "CHECK": .80 if not f.wet else .90}, "pot_control")
    if draw:
        return Policy({"BET_MEDIUM": .45 if s.in_position else .25,
                       "CHECK": .55 if s.in_position else .75}, "draw_mix")
    if f.made == "STRONG" and e >= .57:
        return Policy({"BET_SMALL": .50, "CHECK": .50}, "protection_mix")
    if s.street == "flop" and s.node.last_aggressor == s.hero_seat and not f.wet:
        return Policy({"BET_SMALL": .42 if s.in_position else .25,
                       "CHECK": .58 if s.in_position else .75}, "dry_cbet")
    return Policy({"CHECK": 1}, "no_automatic_barrel")


def exploit(base, s, f, model, cap):
    key, target = None, None
    if s.street == "preflop" and s.node.raises == 0 and s.position == "BTN":
        key, target = "fold_vs_open", Policy({"RAISE_SMALL": 1})
    elif (s.street == "preflop" and s.node.id == "PREFLOP_BB_VS_OPEN"
          and f.strength >= .46):
        key, target = "fold_to_3bet", Policy({"RAISE_MEDIUM": 1})
    elif s.street != "preflop" and not s.to_call and f.made in {"AIR", "DRAW"}:
        key, target = f"fold_to_{s.street}_bet", Policy({"BET_SMALL": 1})
    if key is None or key not in model.stats:
        return base.normalized(), {"weight": 0}
    stat = model.stats[key]
    effect = max(0, stat.mean-stat.prior-.08)
    weight = min(cap, effect*stat.confidence)
    if stat.opportunities < 12:
        weight = 0
    return base.mix(target, weight), {"key": key, "weight": round(weight, 5),
                                     "evidence": stat.summary()}


def preflop_factor(label):
    value = int(label.rsplit("_", 1)[1])
    return float(value) if value < 10 else value/10 if value < 100 else value/100


def sizing(label, s):
    if label == "ALL_IN":
        return s.max_raise
    if s.street == "preflop":
        explicit = (("OPEN_", s.big_blind),
                    ("ISO_", s.big_blind),
                    ("THREEBET_", s.villain_bet),
                    ("RERAISE_", s.villain_bet))
        for prefix, base in explicit:
            if label.startswith(prefix):
                return round(base*preflop_factor(label))
        n = s.node.raises
        if n == 0:
            factor = 3.5 if s.position == "BB" else 2 if s.effective_stack_bb <= 25 else 2.25
            return round(factor*s.big_blind)
        multiplier = (3.3 if not s.in_position else 2.8) if n == 1 else 2.25
        return round(s.villain_bet*multiplier)
    fraction = {"BET_SMALL": .33, "BET_MEDIUM": .66, "BET_LARGE": 1,
                "BET_OVERBET": 1.25, "RAISE_SMALL": .5, "RAISE_MEDIUM": .75,
                "RAISE_LARGE": 1}.get(label, .5)
    # Call first, then raise by fraction of pot AFTER calling; output street TOTAL.
    return s.hero_bet+s.to_call+round(fraction*(s.pot+s.to_call))


def safe_action(valid):
    for action in ("check", "fold", "call"):
        if action in valid:
            return Action(action)
    raise ValueError("No safe NLHE action offered")


def legalize(label, s):
    direct = {"FOLD": "fold", "CHECK": "check", "CALL": "call"}
    if label in direct and direct[label] in s.valid_actions:
        return Action(direct[label]), None
    if label.startswith(("BET_", "RAISE_", "OPEN_", "ISO_", "THREEBET_",
                         "RERAISE_")) or label == "ALL_IN":
        if "raise" in s.valid_actions and 0 < s.min_raise <= s.max_raise:
            return Action.raise_to(max(s.min_raise, min(s.max_raise, sizing(label, s)))), None
        if label == "ALL_IN" and "all_in" in s.valid_actions:
            return Action.all_in(), None
        if "call" in s.valid_actions:
            return Action.call(), "raise_unavailable_call"
    if label == "CALL" and "check" in s.valid_actions:
        return Action.check(), "free_call"
    return safe_action(s.valid_actions), "unavailable_abstraction"


def select(policy, rng):
    sample, running = rng.random(), 0.0
    weights = policy.normalized().weights
    for label, weight in sorted(weights.items()):
        running += weight
        if sample < running:
            return label, sample
    return sorted(weights)[-1], sample
