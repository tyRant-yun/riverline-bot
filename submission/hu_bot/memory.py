"""Match-local observations, counted once from canonical hand histories."""
from collections import deque
from dataclasses import dataclass

from .history import ledger
from .opponent_actions import MAX_ACTION_CELLS, observe_action
from .responses import observe_response
from .profile_signals import first_river_action, revealed_overbet_strength
from .preflop_observation import OpportunityLedger


@dataclass
class Posterior:
    prior: float = .5
    prior_weight: float = 20.0
    success: int = 0
    opportunities: int = 0

    @property
    def mean(self):
        return (self.prior*self.prior_weight+self.success)/(self.prior_weight+self.opportunities)

    @property
    def confidence(self):
        return self.opportunities/(self.opportunities+40)

    def observe(self, success):
        self.success += bool(success)
        self.opportunities += 1

    def summary(self):
        return {"success": self.success, "opportunities": self.opportunities,
                "prior_weight": self.prior_weight,
                "prior": self.prior, "posterior": round(self.mean, 4),
                "confidence": round(self.confidence, 4)}


class OpponentModel:
    def __init__(self, enabled=True, action_cell_limit=MAX_ACTION_CELLS):
        self.enabled = enabled
        self.stats = {}
        self.population_priors = {}
        self.showdowns = deque(maxlen=64)
        # Keep exact IDs until match reset; bounded eviction double-counts long games.
        self._seen = set()
        self.action_cell_limit = max(1, min(MAX_ACTION_CELLS, int(action_cell_limit)))
        self.action_cells = {}
        self.preflop = OpportunityLedger()

    def stat(self, key, prior=.5):
        if key not in self.stats:
            self.stats[key] = Posterior(prior)
            self.population_priors[key] = prior
        return self.stats[key]

    def observe_hand(self, key, result, hero_seat, dealer_seat, board=(),
                     big_blind=10, depth_bb=None):
        if not self.enabled:
            return
        if key in self._seen:
            return
        self._seen.add(key)
        self.preflop.observe_hand(key, result, hero_seat, dealer_seat, big_blind, depth_bb)
        history = result.get("action_history", [])
        raises, last_pf, previous_street_aggressor = 0, None, None
        current_street, street_raises, checked, aggressor = "preflop", 0, set(), None
        contexts = ledger(history)
        revealed_overbet_key = None
        for index, e in enumerate(history):
            event = contexts[index]
            observe_response(self, event, hero_seat, dealer_seat)
            observe_action(self, event, hero_seat, dealer_seat)
            action, seat = e["action"], e["seat"]
            street = e.get("phase", "preflop")
            if action.startswith("post_"):
                continue
            if street != current_street:
                previous_street_aggressor = last_pf if current_street == "preflop" else aggressor
                current_street, street_raises, checked, aggressor = street, 0, set(), None
            # Legacy wire histories may encode an all-in call as ``all_in``.
            # The ledger's reconstructed target is authoritative for whether
            # the action actually increased the table bet.
            is_aggression = (action == "raise" or
                             action == "all_in" and event.amount_to > event.table_bet)
            is_call = action == "call" or (
                action == "all_in" and event.amount_to <= event.table_bet)
            if seat != hero_seat and not e.get("is_timeout", False):
                first_key = first_river_action(self, event, hero_seat, dealer_seat,
                                               hero_seat in checked)
                if first_key is not None:
                    revealed_overbet_key = first_key
                if street == "preflop":
                    if raises == 0 and seat == dealer_seat:
                        self.stat("btn_open", .70).observe(is_aggression)
                        self.stat("btn_limp", .20).observe(is_call)
                    if raises == 1 and last_pf == hero_seat:
                        if seat != dealer_seat:
                            self.stat("fold_vs_open", .30).observe(action == "fold")
                            self.stat("three_bet", .18).observe(is_aggression)
                        else:
                            self.stat("fold_to_isolation", .35).observe(action == "fold")
                            self.stat("isolation_reraise", .12).observe(is_aggression)
                    if raises == 0 and seat != dealer_seat:
                        self.stat("isolate", .35).observe(is_aggression)
                    if raises == 2 and last_pf == hero_seat:
                        key = ("fold_to_3bet" if hero_seat != dealer_seat
                               else "fold_to_limp_reraise")
                        self.stat(key, .45).observe(action == "fold")
                        self.stat("four_bet", .12).observe(is_aggression)
                else:
                    if street_raises == 0:
                        self.stat(f"{street}_bet", .45).observe(is_aggression)
                        if previous_street_aggressor == seat:
                            self.stat("flop_cbet" if street == "flop" else f"{street}_barrel", .55).observe(is_aggression)
                        elif street == "flop" and last_pf == hero_seat and seat != dealer_seat:
                            self.stat("flop_donk", .12).observe(is_aggression)
                    elif aggressor == hero_seat:
                        key = (f"fold_to_{street}_bet" if street_raises == 1
                               else f"fold_to_{street}_raise")
                        self.stat(key, .40).observe(action == "fold")
                        self.stat(f"{street}_raise", .12).observe(is_aggression)
                        if seat in checked:
                            self.stat(f"{street}_check_raise", .12).observe(is_aggression)
                    if is_aggression:
                        ratio = contexts[index].size_ratio
                        self.stat(f"{street}_overbet", .10).observe(ratio > 1)
                        self.stat(f"{street}_large_bet", .25).observe(ratio >= .75)
            if action == "check":
                checked.add(seat)
            if is_aggression:
                street_raises += 1
                aggressor = seat
                if street == "preflop":
                    raises += 1
                    last_pf = seat
        for showdown in result.get("showdown", []) or []:
            if showdown.get("seat") != hero_seat:
                self.showdowns.append({"hand": key, "hole_cards": showdown.get("hole_cards", []),
                    "board": list(board), "hand_rank": showdown.get("hand_rank"),
                    "line": history[-32:]})
                revealed_overbet_strength(self, revealed_overbet_key,
                                          showdown.get("hole_cards", []), board)

    def summary(self):
        return {k: v.summary() for k, v in sorted(self.stats.items())}

    def range_adjustment(self, key):
        """Small threshold shift from smoothed observed frequency, after 20 opportunities.

        Positive = broader aggression range; this is not direct bluff-frequency evidence.
        """
        stat = self.stats.get(key)
        if stat is None or stat.opportunities < 20:
            return 0.0
        return max(-.12, min(.12, .5*(stat.mean-stat.prior)*stat.confidence))
