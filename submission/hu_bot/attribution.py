"""Pure decision attribution helpers; no strategy or RNG side effects."""

from .strategy import legalize, select
from .core import Policy


def policy_l1(left, right):
    """L1 distance between two action distributions."""
    if left is None or right is None:
        return None
    keys = set(left) | set(right)
    return round(sum(abs(left.get(key, 0.0) - right.get(key, 0.0)) for key in keys), 10)


def _action_key(action):
    data = action.to_dict()
    return data.get("action"), data.get("amount")


def attribution(original, river_mixed, final, river, sample, state,
                river_mix_weight=0.0, generic_adjustment=None, fallback=None,
                status="complete", reason_flags=None):
    """Build trace-only attribution without consuming the live selector RNG."""
    candidates = river.get("candidates", []) if isinstance(river, dict) else []
    calibrated = [c for c in candidates
                  if c.get("evidence") and c["evidence"].get("weight", 0) > 0]
    selected = river.get("selected") if isinstance(river, dict) else None
    selected_candidate = next((c for c in candidates if c.get("label") == selected), None)
    selected_evidence = bool(selected_candidate and selected_candidate.get("evidence")
                             and selected_candidate["evidence"].get("weight", 0) > 0)

    baseline_label, baseline_sample = select(Policy(original), _FixedRandom(sample))
    final_label, final_sample = select(Policy(final), _FixedRandom(sample))
    baseline_action, baseline_remap = legalize(baseline_label, state)
    final_action, final_remap = legalize(final_label, state)
    same_action = _action_key(baseline_action) == _action_key(final_action)
    generic_adjustment = generic_adjustment or {}
    generic_weight = float(generic_adjustment.get("weight", 0.0) or 0.0)
    return {
        "evidence_available": any(c.get("evidence") is not None for c in candidates),
        "calibrated_candidate_count": len(calibrated),
        "selected_target_evidence": selected_evidence,
        "river_ev_applied_mix_weight": round(float(river_mix_weight), 10),
        "generic_exploit_applied": generic_weight > 0,
        "generic_exploit_weight": round(generic_weight, 10),
        "policy_L1_change": {
            "comparator": "original_base_to_final",
            "river_ev": policy_l1(original, river_mixed),
            "generic_exploit": policy_l1(river_mixed, final),
            "total": policy_l1(original, final),
            "response_only": None,
        },
        "sampled_action_changed_same_state_rng": not same_action,
        "counterfactual": {
            "comparator": "original_base_to_final",
            "same_state_same_rng": True,
            "baseline_label": baseline_label,
            "final_label": final_label,
            "baseline_action": baseline_action.to_dict(),
            "final_action": final_action.to_dict(),
            "baseline_legal_remap": baseline_remap,
            "final_legal_remap": final_remap,
            "sample": sample,
        },
        "status": status,
        "reason_flags": reason_flags or {},
        "fallback_class": fallback,
    }


class _FixedRandom:
    """random.Random-compatible one-shot source for select()."""

    def __init__(self, value):
        self.value = float(value)

    def random(self):
        return self.value
