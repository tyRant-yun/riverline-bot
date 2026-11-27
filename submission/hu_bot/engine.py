"""SDK-independent orchestration; all expensive work shares one wall-clock budget."""
from collections import Counter
from dataclasses import asdict, replace
import json
import logging
import random
from time import perf_counter

from .config import Config
from . import __version__
from .accounting import DecisionSnapshot, RuleSnapshot
from .preflop import TABLE_SHA256, context_for_state
from .core import RNGManager, normalize
from .memory import OpponentModel
from .river import plan as river_plan
from .poker import features
from .ranges import RangeState, EquityResult, posterior, equity
from .strategy import (preflop_policy, postflop_policy, exploit, legalize, select,
                       safe_action)
from .attribution import attribution
from .terminal import guaranteed_royal_call, terminal_decision
from .defense import defend_river_overbet
from .facing_river import guard_unpriced_raise
from .value_optimizer import plan_value
from .preflop_exploit import ExploitPlanner
from .strength_slice import assess_slice, gate_summary

logger = logging.getLogger("hu-v1")


class Engine:
    def __init__(self, config=None):
        self.config = config or Config()
        self.reset()

    def reset(self, match_id="offline", big_blind=10, timeout_ms=2000):
        self.match_id = match_id
        self.big_blind = big_blind
        self.timeout_ms = timeout_ms
        self.rng = RNGManager(self.config.seed, match_id)
        self.opponent = OpponentModel(enabled=self.config.learn_opponent)
        self.last_trace = {}
        self.log_bytes = 0
        self.decisions = 0
        self.fallbacks = 0
        self.rules = {}
        self.rule_snapshot = RuleSnapshot.unknown(match_id, big_blind)
        self.hand_start_stacks = {}
        self.hand_depths = {}
        self.preflop_planner = ExploitPlanner()
        self.match_counters = Counter()

    def set_rule_snapshot(self, snapshot):
        self.rule_snapshot = snapshot
        self.rules = snapshot.as_dict()

    def set_hand_start_stacks(self, hand_number, stacks):
        self.hand_start_stacks[int(hand_number)] = tuple(int(value) for value in stacks)

    def evaluate(self, raw, replay_trace=None, profiler=None):
        def mark(stage):
            if profiler is not None:
                profiler.mark(stage)

        start = perf_counter()
        budget = max(1, min(self.config.decision_budget_ms, self.timeout_ms*.50)) / 1000
        deadline = start+budget
        s = normalize(raw, self.big_blind)
        accounting = DecisionSnapshot.from_game_state(
            raw, rules=self.rule_snapshot,
            hand_start_stacks=self.hand_start_stacks.get(raw.hand_number))
        mark("normalized")
        self.big_blind = s.big_blind
        if s.street == "preflop":
            self.hand_depths.setdefault(s.hand_number, s.effective_stack_bb)
        if s.num_players != 2:
            raise ValueError("HU v1 requires exactly two seats")
        if len(s.hero_cards) != 2 or len(set(s.hero_cards+s.board)) != 2+len(s.board):
            raise ValueError("Missing or duplicate cards")
        f = features(s.hero_cards, s.board)
        mark("features")
        seed, rng = self.rng.for_state(s)
        equity_seed, equity_rng = self.rng.for_state(s, "equity")
        eq = EquityResult(f.strength, 0, .5, False, False, "preflop_chart")
        villain_range = RangeState(s.node.id, {}, 0, 0)
        hero_range = RangeState(s.node.id, {}, 0, 0)
        vr_limit = replay_trace["villain_range"]["last_update"] if replay_trace else None
        hr_limit = replay_trace["hero_range"]["last_update"] if replay_trace else None
        villain_range = posterior(s, 1-s.hero_seat, start+budget*.45, vr_limit,
                                  opponent_model=self.opponent if self.config.adapt_ranges else None,
                                  polar_river=self.config.polar_river_ranges,
                                  action_likelihood_calibration=self.config.action_likelihood_calibration)
        hero_range = posterior(s, s.hero_seat, start+budget*.60, hr_limit)
        mark("ranges")
        terminal = {"status": "not_applicable"}
        preflop_range = {"applied": False, "policy_l1": 0.0,
                         "status": "not_preflop"}
        old_preflop = None
        if s.street == "preflop":
            accounting_safe = accounting.ledger.strategy_safe
            base = preflop_policy(
                s, use_range_spec=self.config.range_repair_enabled and accounting_safe,
                                  include_research=self.config.range_extension_enabled).normalized()
            if self.config.range_repair_enabled:
                from .range_spec import candidate_status
                _, candidate_gate_status = candidate_status(
                    s, self.config.range_extension_enabled)
                preflop_range["status"] = candidate_gate_status
                if not accounting_safe and candidate_gate_status == "applied":
                    preflop_range["status"] = "accounting_inconsistent"
                preflop_range["research_window"] = bool(
                    self.config.range_extension_enabled and
                    55 <= s.effective_stack_bb <= 65 and
                    (s.node.id == "PREFLOP_BB_VS_LIMP" or
                     s.node.id == "PREFLOP_BB_VS_OPEN" and
                     abs(s.villain_bet/s.big_blind-2.5) < 1e-9))
            else:
                preflop_range["status"] = ("accounting_inconsistent"
                                             if not accounting_safe else "disabled")
            if base.reason.startswith("default_range_v183:"):
                preflop_range["applied"] = True
                old_preflop = preflop_policy(s, use_range_spec=False).normalized()
                old_weights = old_preflop.weights
                preflop_range["policy_l1"] = sum(
                    abs(base.weights.get(k, 0)-old_weights.get(k, 0))
                    for k in base.weights.keys() | old_weights.keys())
        else:
            samples = replay_trace["equity"]["samples"] if replay_trace else self.config.max_samples
            force_mc = replay_trace is not None and not replay_trace["equity"]["exact"]
            reserve_river_work = (self.config.river_ev_enabled or self.config.robust_river_defense
                                  or self.config.conditional_value_enabled)
            equity_fraction = .67 if self.config.conditional_value_enabled else .78
            equity_deadline = (start+budget*equity_fraction if s.street == "river"
                               and reserve_river_work else deadline)
            eq = equity(s, villain_range, equity_rng, equity_deadline, samples, force_mc=force_mc)
            mark("equity")
            base = postflop_policy(s, f, eq).normalized()
            override, terminal = guaranteed_royal_call(s)
            if override is None:
                override, terminal = terminal_decision(s, eq, self.config.terminal_ev_enabled)
            if override is not None:
                terminal = {**terminal, "original_policy": base.weights,
                            "applied_policy": override.weights}
                base = override.normalized()
        strength_requested = (self.config.strength_slice_diagnostics or
                              self.config.strength_slice_strategy_enabled)
        strength_slice = assess_slice(
            s, accounting, villain_range, hero_range, eq,
            elapsed_ms=(perf_counter() - start) * 1000,
            budget_ms=max(1, min(self.config.decision_budget_ms, self.timeout_ms * .50)),
        ) if strength_requested else {
            "status": "diagnostics_disabled",
            "strategy_gate": {"module": "v1.10_vertical_slice", "enabled": False,
                               "passed": False, "reason": "diagnostics_disabled"},
        }
        if self.config.strength_slice_strategy_enabled and strength_slice:
            strength_slice["strategy_gate"]["requested"] = True
        if strength_requested:
            gate = gate_summary(strength_slice)
            self.match_counters["strength_slice_decisions"] += 1
            if not gate["passed"]:
                self.match_counters["strength_slice_gate_closed"] += 1
        strength_trace = (strength_slice if self.config.retain_diagnostics
                          else gate_summary(strength_slice))
        raise_guard = {"applied": False, "reason": "not_river"}
        if s.street == "river":
            guard_replay_allows = (replay_trace is None or
                                   replay_trace.get("unpriced_raise_guard", {}).get("applied", False))
            guarded, raise_guard = guard_unpriced_raise(
                s, base, villain_range, eq, deadline,
                enabled=self.config.unpriced_raise_guard and guard_replay_allows)
            if guarded is not None:
                base = guarded
        defense = {"applied": False, "reason": "not_river"}
        if s.street == "river":
            defense_replay_allows = (replay_trace is None or
                                    replay_trace.get("river_defense", {}).get("applied", False))
            override, defense = defend_river_overbet(s, base, villain_range, eq, deadline,
                enabled=self.config.robust_river_defense and defense_replay_allows,
                mix_weight=self.config.river_defense_mix)
            defense = {**defense, "applied": override is not None}
            if override is not None:
                before, after = base.normalized().weights, override.normalized().weights
                defense["policy_l1"] = sum(abs(before.get(k, 0)-after.get(k, 0))
                                           for k in before.keys() | after.keys())
                base = override.normalized()
        river = {"complete": False, "reason": "disabled_or_protected"}
        conditional_value = {"complete": False, "reason": "not_river"}
        if s.street == "river":
            value_replay_allows = (replay_trace is None or
                                   replay_trace.get("conditional_value", {}).get("applied", False))
            value_policy, conditional_value = plan_value(
                s, villain_range, hero_range, self.opponent, deadline,
                enabled=(self.config.conditional_value_enabled and not eq.nuts
                         and value_replay_allows))
            conditional_value = {**conditional_value, "applied": value_policy is not None}
            if value_policy is not None:
                before, after = base.normalized().weights, value_policy.normalized().weights
                conditional_value["original_policy"] = before
                conditional_value["applied_policy"] = after
                conditional_value["policy_l1"] = sum(abs(before.get(k, 0)-after.get(k, 0))
                                                     for k in before.keys() | after.keys())
                base = value_policy.normalized()
        original_base = base
        river_mix_weight = 0.0
        replay_allows = replay_trace is None or replay_trace.get("river_ev", {}).get("complete", False)
        if (self.config.river_ev_enabled and not conditional_value.get("applied")
                and s.street == "river" and eq.exact
                and not eq.nuts and not f.board_only and replay_allows):
            target, river = river_plan(s, villain_range, hero_range, self.opponent, deadline,
                                      adapt=self.config.adapt_responses)
            if target is not None:
                river_mix_weight = max(0., min(1., self.config.river_ev_mix))
                base = base.mix(target, river_mix_weight)
                base = replace(base, reason="river_ip_response_ev_mix")
        if river["complete"] or conditional_value.get("applied"):
            final, adjustment = base, {"weight": 0, "reason": "response_ev_replaces_generic_river_exploit"}
        else:
            final, adjustment = exploit(base, s, f, self.opponent, self.config.exploit_cap)
        final, preflop_overfold = self.preflop_planner.propose(
            final, s, self.opponent.preflop,
            enabled=self.config.preflop_overfold_enabled,
            cap=self.config.preflop_overfold_cap)
        mark("policy")
        label, sample = select(final, rng)
        action, fallback = legalize(label, s)
        mark("legalized")
        if old_preflop is not None:
            old_final, _ = exploit(old_preflop, s, f, self.opponent,
                                   self.config.exploit_cap)
            old_final, _ = self.preflop_planner.propose(
                old_final, s, self.opponent.preflop,
                enabled=self.config.preflop_overfold_enabled,
                cap=self.config.preflop_overfold_cap)
            old_label, _ = select(old_final, random.Random(seed))
            old_action, _ = legalize(old_label, s)
            preflop_range["sampled_action_changed"] = (old_action.to_dict()
                                                          != action.to_dict())
        if not self.config.retain_diagnostics:
            self.last_trace = {"schema_version": 4, "version": __version__,
                "match": self.match_id, "hand": s.hand_number, "street": s.street,
                "action": action.to_dict(), "fallback": fallback,
                "preflop_range": preflop_range,
                "accounting_status": accounting.status,
                "accounting_conflicts": list(accounting.conflicts),
                "strength_slice": strength_trace,
                "diagnostics_retained": False,
                "elapsed_ms": round((perf_counter()-start)*1000, 3)}
            return action
        if not villain_range.complete or not hero_range.complete:
            attribution_status = "incomplete_range"
        elif fallback:
            attribution_status = "legal_remap"
        else:
            attribution_status = {
                "deadline_atomic_discard": "deadline",
                "outside_60_125bb_start": "unsupported_node",
                "unsupported_node": "unsupported_node",
                "empty_range": "incomplete_range",
            }.get(river.get("reason"), "complete")
        reason_flags = {
            "deadline": river.get("reason") == "deadline_atomic_discard",
            "incomplete_range": not villain_range.complete or not hero_range.complete,
            "unsupported_node": river.get("reason") in {"unsupported_node",
                                                          "outside_60_125bb_start"},
            "insufficient_evidence": None if (
                self.config.adapt_responses and s.street == "river" and
                (river.get("reason") == "deadline_atomic_discard" or
                 not villain_range.complete or not hero_range.complete)) else bool(
                self.config.adapt_responses and river.get("complete")
                and any(c.get("label") != "CHECK" for c in river.get("candidates", []))
                and not any(c.get("evidence") and c["evidence"].get("weight", 0) > 0
                            for c in river["candidates"])),
            "exception": False,
            "legal_remap": bool(fallback),
        }
        decision_attribution = attribution(
            original_base.weights, base.weights, final.weights, river, sample, s,
            river_mix_weight=river_mix_weight,
            generic_adjustment=adjustment,
            fallback=fallback if fallback else None,
            status=attribution_status, reason_flags=reason_flags)
        elapsed = (perf_counter()-start)*1000
        self.last_trace = {
            "schema_version": 4, "version": __version__, "match": self.match_id,
            "preflop_table_sha256": TABLE_SHA256,
            "preflop_context": context_for_state(s) if s.street == "preflop" else None,
            "preflop_range": preflop_range,
            "accounting": accounting.summary(),
            "range_model": ("historical-ledger+" + villain_range.river_components["model"]
                            if villain_range.river_components is not None else
                            "historical-ledger+chart+bounded-opponent-v1.1"),
            "hand": s.hand_number, "street": s.street, "node": s.node.id,
            "last_aggressor": s.node.last_aggressor,
            "hole": s.hero_cards, "board": s.board, "position": s.position,
            "effective_stack_bb": round(s.effective_stack_bb, 3),
            "stack_bucket": s.stack_bucket, "spr": round(s.spr, 3),
            "pot": s.pot, "to_call": s.to_call, "hero_stack": s.hero_stack,
            "rules": {"initial": self.rules, "current_big_blind": s.big_blind,
                      "posted_antes": [e["amount"] for e in s.action_history if e["action"] == "post_ante"]},
            "features": asdict(f), "equity": asdict(eq),
            "pot_odds": s.to_call/max(1, s.pot+s.to_call),
            "call_ev_checkdown": eq.equity*(s.pot+s.to_call)-s.to_call,
            "hero_range": hero_range.summary(), "villain_range": villain_range.summary(),
            "base_policy": base.weights, "exploit": adjustment, "policy": final.weights,
            "preflop_overfold": preflop_overfold,
            "original_base_policy": original_base.weights, "river_ev": river,
            "terminal_decision": terminal,
            "unpriced_raise_guard": raise_guard,
            "conditional_value": conditional_value,
            "river_defense": defense,
            "strength_slice": strength_slice,
            "reason": base.reason, "rng_seed": seed, "equity_seed": equity_seed,
            "random": sample, "selected": label, "action": action.to_dict(),
            "fallback": fallback, "elapsed_ms": round(elapsed, 3),
            "attribution": decision_attribution,
            # Replay includes full state; sample count permits deterministic reruns after deadline cutoffs.
            "replay": {"state": asdict(raw), "match_id": self.match_id,
                       "big_blind": self.big_blind, "seed": self.config.seed,
                       "max_samples": self.config.max_samples,
                       "config": asdict(self.config),
                       "opponent": self.opponent.summary()},
        }
        return action

    def decide(self, raw):
        start = perf_counter()
        self.decisions += 1
        try:
            action = self.evaluate(raw)
        except Exception as exc:
            self.fallbacks += 1
            action = safe_action(raw.valid_actions)
            self.last_trace = {"schema_version": 4, "version": __version__, "match": self.match_id,
                "hand": raw.hand_number, "street": raw.phase,
                "action": action.to_dict(), "fallback": type(exc).__name__+": "+str(exc),
                "elapsed_ms": round((perf_counter()-start)*1000, 3),
                "attribution": {"evidence_available": None,
                    "calibrated_candidate_count": None,
                    "selected_target_evidence": None,
                    "river_ev_applied_mix_weight": None,
                    "generic_exploit_applied": None,
                    "generic_exploit_weight": None,
                    "policy_L1_change": {"comparator": "original_base_to_final",
                        "river_ev": None, "generic_exploit": None,
                        "total": None, "response_only": None},
                    "sampled_action_changed_same_state_rng": None,
                    "counterfactual": None, "status": "exception",
                    "reason_flags": {"deadline": None, "incomplete_range": None,
                        "unsupported_node": None, "insufficient_evidence": None,
                        "exception": True, "legal_remap": None},
                    "fallback_class": "engine_exception"}}
            if self.config.retain_diagnostics:
                logger.exception("FALLBACK match=%s hand=%s phase=%s reason=engine_exception cz_action=%s elapsed_ms=%.3f",
                                 self.match_id, raw.hand_number, raw.phase, action.action,
                                 self.last_trace["elapsed_ms"])
            else:
                self.last_trace["fallback"] = self.last_trace["fallback"][:240]
                self.last_trace.pop("attribution", None)
                self.last_trace["diagnostics_retained"] = False
                if self.fallbacks <= 3:
                    logger.error("FALLBACK match=%s hand=%s reason=%s",
                                 self.match_id, raw.hand_number, self.last_trace["fallback"])
        if self.config.trace:
            self.emit_trace(raw, action)
        status = self.last_trace.get("preflop_range", {}).get("status")
        if status in {"applied", "out_of_scope", "history_price_mismatch", "disabled"}:
            self.match_counters["range_" + status] += 1
        if self.last_trace.get("preflop_range", {}).get("sampled_action_changed"):
            self.match_counters["range_sampled_action_changed"] += 1
        if self.last_trace.get("preflop_range", {}).get("policy_l1", 0) > 1e-9:
            self.match_counters["range_policy_changed"] += 1
        if self.last_trace.get("fallback"):
            category = ("legal_remap" if self.last_trace["fallback"] in
                        {"raise_unavailable_call", "free_call", "unavailable_abstraction"}
                        else "engine_exception")
            self.match_counters[category] += 1
        if self.last_trace.get("elapsed_ms", 0) > self.config.decision_budget_ms:
            self.match_counters["over_budget_decisions"] += 1
        return action

    def emit_trace(self, raw, action):
        if not self.config.retain_diagnostics:
            return
        trace = self.last_trace
        # Reserve space under the platform's 2 MiB cap; never write runtime files.
        payload = json.dumps(trace, separators=(",", ":"), ensure_ascii=True)
        if self.log_bytes+len(payload)+500 > self.config.log_byte_budget:
            if self.decisions % 25 == 0:
                logger.info("SUMMARY decisions=%s fallbacks=%s trace_budget_exhausted=true",
                            self.decisions, self.fallbacks)
            return
        logger.info("DECIDE match=%s hand=%s phase=%s board=%s hole=%s stack=%s pot=%s to_call=%s legal_cz=[%s] legal_pb=[%s] pb_label=%s cz_action=%s elapsed_ms=%.3f drain_ms=0",
            self.match_id, raw.hand_number, raw.phase, ",".join(map(str, raw.board)) or "-",
            ",".join(map(str, raw.hole_cards)), raw.your_stack, raw.pot, raw.to_call,
            ",".join(raw.valid_actions), ",".join(trace.get("policy", {})),
            trace.get("selected", "fallback"), action.action, trace["elapsed_ms"])
        logger.info("TRACE %s", payload)
        self.log_bytes += len(payload)+500
