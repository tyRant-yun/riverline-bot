from dataclasses import replace

from hu_bot import __version__
from hu_bot.attribution import attribution
from hu_bot.config import Config
from hu_bot.core import normalize
from hu_bot.engine import Engine
from lab.scenarios import scenarios
from bot import HUBot


def _state(name="river_giveup"):
    return normalize(scenarios()[name])


def _river(evidence=None, selected="BET_SMALL"):
    candidate = {"label": selected, "evidence": evidence}
    return {"complete": True, "selected": selected, "candidates": [candidate]}


def test_evidence_count_requires_positive_applied_weight():
    detail = _river({"weight": 0})
    trace = attribution({"CHECK": 1}, {"CHECK": 1}, {"CHECK": 1}, detail,
                         .2, _state())
    assert trace["evidence_available"] is True
    assert trace["calibrated_candidate_count"] == 0
    detail = _river({"weight": .2})
    trace = attribution({"CHECK": 1}, {"CHECK": 1}, {"CHECK": 1}, detail,
                         .2, _state())
    assert trace["evidence_available"] and trace["calibrated_candidate_count"] == 1


def test_selected_evidence_is_separate_from_any_candidate_evidence():
    detail = {"complete": True, "selected": "CHECK", "candidates": [
        {"label": "BET_SMALL", "evidence": {"weight": .5}},
        {"label": "CHECK", "evidence": None},
    ]}
    trace = attribution({"CHECK": 1}, {"CHECK": 1}, {"CHECK": 1}, detail,
                         .2, _state())
    assert trace["evidence_available"] and trace["calibrated_candidate_count"] == 1
    assert not trace["selected_target_evidence"]


def test_policy_l1_and_comparator_are_explicit():
    detail = _river({"weight": .5})
    trace = attribution({"CHECK": .5, "BET_SMALL": .5},
                         {"CHECK": 0, "BET_SMALL": 1},
                         {"CHECK": 1}, detail, .9, _state())
    assert trace["policy_L1_change"]["comparator"] == "original_base_to_final"
    assert trace["policy_L1_change"]["river_ev"] == 1.0
    assert trace["policy_L1_change"]["generic_exploit"] == 2.0
    assert trace["policy_L1_change"]["response_only"] is None


def test_same_rng_compares_legalized_action_and_amount():
    state = _state()
    detail = _river(None)
    trace = attribution({"BET_SMALL": 1}, {"BET_SMALL": 1},
                         {"BET_LARGE": 1}, detail, .1, state)
    assert trace["counterfactual"]["comparator"] == "original_base_to_final"
    assert trace["sampled_action_changed_same_state_rng"]
    assert trace["counterfactual"]["baseline_action"] != trace["counterfactual"]["final_action"]


def test_distribution_change_can_leave_same_sampled_action():
    trace = attribution({"CHECK": .5, "BET_SMALL": .5},
                         {"CHECK": .6, "BET_SMALL": .4},
                         {"CHECK": .6, "BET_SMALL": .4}, _river(None), .1, _state())
    assert trace["policy_L1_change"]["total"] == .2
    assert not trace["sampled_action_changed_same_state_rng"]


def test_label_change_with_same_clamped_amount_is_not_action_change():
    state = replace(_state(), min_raise=1, max_raise=1)
    trace = attribution({"BET_SMALL": 1}, {"BET_SMALL": 1},
                         {"BET_LARGE": 1}, _river(None), .1, state)
    assert trace["counterfactual"]["baseline_label"] != trace["counterfactual"]["final_label"]
    assert trace["counterfactual"]["baseline_action"] == trace["counterfactual"]["final_action"]
    assert not trace["sampled_action_changed_same_state_rng"]


def test_reason_flags_preserve_deadline_range_and_remap_coexistence():
    flags = {"deadline": True, "incomplete_range": True, "unsupported_node": False,
             "insufficient_evidence": None, "exception": False, "legal_remap": True}
    trace = attribution({"CHECK": 1}, {"CHECK": 1}, {"CHECK": 1},
                         {"complete": False, "reason": "deadline_atomic_discard"},
                         .1, _state(), reason_flags=flags, status="deadline")
    assert trace["reason_flags"] == flags


def test_direct_decide_and_exception_trace_keep_version_and_attribution():
    engine = Engine(Config(trace=False))
    action = engine.decide(scenarios()["flop_combo_draw"])
    assert action.action in scenarios()["flop_combo_draw"].valid_actions
    assert engine.last_trace["version"] == __version__
    assert engine.last_trace["schema_version"] == 4
    assert engine.last_trace["attribution"]["status"] == "complete"
    assert "generic_exploit_weight" in engine.last_trace["attribution"]

    bad = replace(scenarios()["flop_combo_draw"], opponent_stacks=[1000, 1000])
    action = engine.decide(bad)
    assert action.action in bad.valid_actions
    assert engine.last_trace["version"] == __version__
    assert engine.last_trace["attribution"]["status"] == "exception"
    assert engine.last_trace["attribution"]["fallback_class"] == "engine_exception"
    assert engine.last_trace["attribution"]["reason_flags"]["exception"]


def test_lazy_transport_path_keeps_direct_decide_contract():
    bot = HUBot()
    assert bot.engine is None
    state = scenarios()["flop_combo_draw"]
    action = bot.decide(state)
    assert bot.engine is not None
    assert action.action in state.valid_actions
    assert bot.engine.last_trace["diagnostics_retained"] is False
    assert "attribution" not in bot.engine.last_trace


def test_old_replay_trace_still_runs_without_attribution():
    engine = Engine(Config(trace=False))
    state = scenarios()["river_giveup"]
    engine.evaluate(state)
    old = dict(engine.last_trace)
    old.pop("attribution", None)
    old["schema_version"] = 3
    replay = Engine(Config(trace=False))
    replay.evaluate(state, replay_trace=old)
    assert replay.last_trace["version"] == __version__
    assert "attribution" in replay.last_trace
    for key in ("policy", "action", "random"):
        assert replay.last_trace[key] == old[key]
