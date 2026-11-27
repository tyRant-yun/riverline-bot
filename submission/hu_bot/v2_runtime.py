"""v2 SDK-independent main path: facts -> baseline policy -> legal action."""

from collections import Counter
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeout
from time import perf_counter

from . import __version__
from .v2_contracts import capture
from .v2_policy import choose, legalize, preflop_price_candidates


class V2Runtime:
    def __init__(self, seed=20261012):
        self.seed = seed
        self._policy_executor = ThreadPoolExecutor(max_workers=1,
                                                    thread_name_prefix="hu-v2-policy")
        self._policy_future = None
        self._policy_generation = 0
        self.reset()

    def reset(self, match_id="offline", big_blind=10, timeout_ms=2000):
        self.match_id = match_id
        self.big_blind = max(1, int(big_blind))
        self.timeout_ms = max(1, int(timeout_ms))
        self.hand_start_stacks = {}
        self.last_trace = {}
        self.decisions = 0
        self.fallbacks = 0
        self.match_counters = Counter()
        self._policy_generation += 1
        if self._policy_future is not None and self._policy_future.done():
            self._policy_future = None
        # Start the sole worker outside any turn request.  This removes thread
        # creation from the first decision's remaining budget; it is not a
        # strategy computation and publishes no candidate.
        if self._policy_future is None:
            self._policy_executor.submit(lambda: None).result()

    def set_hand_start_stacks(self, hand_number, stacks):
        if len(stacks) == 2:
            self.hand_start_stacks[int(hand_number)] = tuple(int(value) for value in stacks)

    @staticmethod
    def safe_action(snapshot):
        from chipzen import Action
        for name, factory in (("check", Action.check), ("call", Action.call),
                              ("fold", Action.fold)):
            if name in snapshot.valid_actions:
                return factory(), f"safe_{name}"
        if "raise" in snapshot.valid_actions and snapshot.min_raise <= snapshot.max_raise:
            return Action.raise_to(snapshot.min_raise), "safe_min_raise"
        if "all_in" in snapshot.valid_actions:
            return Action.all_in(), "safe_all_in"
        raise ValueError("No legal safe action")

    @staticmethod
    def safe_action_raw(raw):
        """Build L0 directly from SDK authority when facts parsing fails."""
        from chipzen import Action
        valid = set(str(value) for value in getattr(raw, "valid_actions", ()))
        to_call = max(0, int(getattr(raw, "to_call", 0)))
        if to_call and "fold" in valid:
            return Action.fold(), "raw_safe_fold"
        if not to_call and "check" in valid:
            return Action.check(), "raw_safe_check"
        if "call" in valid:
            return Action.call(), "raw_safe_call"
        if "fold" in valid:
            return Action.fold(), "raw_safe_fold"
        if ("raise" in valid and int(getattr(raw, "min_raise", 1))
                <= int(getattr(raw, "max_raise", 0))):
            return Action.raise_to(int(raw.min_raise)), "raw_safe_min_raise"
        if "all_in" in valid:
            return Action.all_in(), "raw_safe_all_in"
        raise ValueError("No legal raw safe action")

    def decide(self, raw):
        started = perf_counter()
        self.decisions += 1
        snapshot = None
        fallback_reason = None
        try:
            snapshot = capture(raw, self.big_blind,
                               self.hand_start_stacks.get(int(raw.hand_number)))
            safe, safe_reason = self.safe_action(snapshot)
            budget_ms = min(450.0, self.timeout_ms * .5)
            deadline = started + budget_ms / 1000.0

            def policy_step():
                decision, hand_features = choose(snapshot)
                action, legalization_reason = legalize(snapshot, decision)
                return decision, hand_features, action, legalization_reason

            # One pre-warmed worker is shared by the runtime.  A late task is
            # never cancelled under the false assumption that Python stopped
            # it: it remains quarantined, and no second task is queued until it
            # completes.  The parent owns the safe action and all publication.
            worker_status = "ready"
            if self._policy_future is not None and not self._policy_future.done():
                worker_status = "busy_late_task"
                action, decision = safe, type("Decision", (), {
                    "label": safe_reason, "reason": "local_worker_busy_safe_action",
                    "strength": 0.0, "model_status": "unscorable"})()
                hand_features = None
                legalization_reason = "local_worker_busy"
            else:
                self._policy_future = self._policy_executor.submit(policy_step)
                future = self._policy_future
                try:
                    decision, hand_features, action, legalization_reason = future.result(
                        timeout=max(.001, deadline - perf_counter()))
                except FutureTimeout:
                    worker_status = "late_task_quarantined"
                    action, decision = safe, type("Decision", (), {
                        "label": safe_reason, "reason": "local_deadline_safe_action",
                        "strength": 0.0, "model_status": "unscorable"})()
                    hand_features = None
                    legalization_reason = "local_deadline"
                else:
                    self._policy_future = None
                    if action.action not in snapshot.valid_actions:
                        action, legalization_reason = safe, "post_legalize_validation"
            fallback_reason = legalization_reason
            action_source = ("v2_conservative_baseline"
                             if fallback_reason != "local_deadline"
                             else "v2_parent_deadline_safe_fallback")
            safe_label = safe_reason
        except Exception as exc:  # keep the transport path alive on malformed state
            self.fallbacks += 1
            self.match_counters["v2_fallbacks"] += 1
            if snapshot is None:
                action, safe_label = self.safe_action_raw(raw)
                elapsed_ms = (perf_counter() - started) * 1000
                self.last_trace = {
                    "version": __version__, "path": "v2",
                    "action": action.to_dict(),
                    "policy": {"label": safe_label, "reason": "raw_facts_safe_baseline",
                                "strength_proxy": 0.0, "source": "v2_raw_safe_fallback",
                                "model_status": "unscorable"},
                    "features": None,
                    "snapshot": {"street": str(getattr(raw, "phase", "unknown")),
                                 "position": "unknown", "pot": int(getattr(raw, "pot", 0)),
                                 "to_call": int(getattr(raw, "to_call", 0)),
                                 "valid_actions": list(getattr(raw, "valid_actions", ()))},
                    "node_key": {"digest": None, "node_id": "unscorable",
                                 "legal_actions": list(getattr(raw, "valid_actions", ()))},
                    "ledger": {"status": "unscorable"},
                    "action_value": {"status": "model_limited",
                                      "reason": "facts_capture_failed"},
                    "information_set": "hero_cards_plus_public_state_only",
                    "safe_action": safe_label, "fallback_reason": type(exc).__name__,
                    "resource": {"elapsed_ms": round(elapsed_ms, 3),
                                  "budget_ms": min(450.0, self.timeout_ms * .5),
                                  "within_budget": True, "worker_status": "facts_failed",
                                  "generation": self._policy_generation},
                }
                return action
            action, safe_label = self.safe_action(snapshot)
            decision = type("Decision", (), {"label": safe_label, "reason": "exception_fallback",
                                              "strength": 0.0, "model_status": "unscorable"})()
            hand_features = None
            action_source = "v2_safe_fallback"
            worker_status = "exception_fallback"
            fallback_reason = type(exc).__name__
        elapsed_ms = (perf_counter() - started) * 1000
        budget_ms = min(450.0, self.timeout_ms * .5)
        self.last_trace = {
            "version": __version__,
            "path": "v2",
            "action": action.to_dict(),
            "policy": {"label": decision.label, "reason": decision.reason,
                        "strength_proxy": round(float(decision.strength), 4),
                        "source": action_source, "model_status": decision.model_status},
            "features": ({"made": hand_features.made, "category": hand_features.category,
                          "draws": list(hand_features.draws), "texture": list(hand_features.texture),
                          "strength_proxy": round(hand_features.strength, 4)}
                         if hand_features is not None else None),
            "snapshot": {"street": snapshot.street, "position": snapshot.position,
                         "pot": snapshot.pot, "to_call": snapshot.to_call,
                         "preflop_context": snapshot.preflop_context,
                         "preflop_price_key": snapshot.preflop_price_key,
                         "preflop_prices": dict(snapshot.preflop_prices),
                         "preflop_off_grid": snapshot.preflop_off_grid,
                         "facing_opponent_bet": snapshot.facing_opponent_bet,
                         "hero_stack": snapshot.hero_stack, "villain_stack": snapshot.villain_stack,
                         "effective_stack_bb": snapshot.effective_stack_bb,
                         "valid_actions": list(snapshot.valid_actions)},
            "preflop_candidates": (preflop_price_candidates(snapshot)
                                    if snapshot.street == "preflop" else None),
            "node_key": {"digest": snapshot.node_key.digest, "node_id": snapshot.node_id,
                         "legal_actions": list(snapshot.node_key.legal_actions),
                         "price_key": snapshot.node_key.preflop_price_key,
                         "open_to": snapshot.node_key.open_to,
                         "three_bet_to": snapshot.node_key.three_bet_to,
                         "four_bet_to": snapshot.node_key.four_bet_to,
                         "history_validity": snapshot.node_key.history_validity,
                         "off_grid": snapshot.node_key.off_grid},
            "ledger": {"displayed_pot": snapshot.ledger.displayed_pot,
                        "matched_contestable_pot": snapshot.ledger.matched_contestable_pot,
                        "pending_unmatched_wager": snapshot.ledger.pending_unmatched_wager,
                        "status": snapshot.ledger.status},
            "action_value": {"status": "model_limited",
                              "reason": "baseline_policy_has_no_conditioned_opponent_Q"},
            "information_set": "hero_cards_plus_public_state_only",
            "safe_action": safe_label,
            "fallback_reason": fallback_reason,
            "resource": {"elapsed_ms": round(elapsed_ms, 3),
                          "budget_ms": budget_ms,
                          "within_budget": elapsed_ms <= budget_ms,
                          "worker_status": worker_status,
                          "generation": self._policy_generation},
        }
        return_elapsed_ms = (perf_counter() - started) * 1000
        if return_elapsed_ms > budget_ms:
            self.match_counters["v2_local_budget_overrun"] += 1
        self.last_trace["resource"]["return_elapsed_ms"] = round(return_elapsed_ms, 3)
        self.last_trace["resource"]["within_budget"] = return_elapsed_ms <= budget_ms
        return action

