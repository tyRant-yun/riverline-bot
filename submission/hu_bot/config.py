from dataclasses import dataclass


@dataclass(frozen=True)
class Config:
    seed: int = 20260921
    decision_budget_ms: int = 450
    max_samples: int = 900
    min_samples: int = 64
    exploit_cap: float = 0.15
    log_byte_budget: int = 1_500_000
    trace: bool = True
    retain_diagnostics: bool = True
    learn_opponent: bool = True
    adapt_ranges: bool = True
    adapt_responses: bool = True
    river_ev_enabled: bool = True
    river_ev_mix: float = 0.5
    terminal_ev_enabled: bool = False
    polar_river_ranges: bool = False
    robust_river_defense: bool = False
    river_defense_mix: float = 0.35
    unpriced_raise_guard: bool = False
    conditional_value_enabled: bool = False
    # Separate research policy request from explicit offline evidence diagnostics.
    # Neither can change policy while the action-value gate is incomplete.
    strength_slice_strategy_enabled: bool = False
    strength_slice_diagnostics: bool = False
    action_likelihood_calibration: bool = False
    # Research entry: only the exact 100BB / no-ante BTN first-in node.
    preflop_overfold_enabled: bool = False
    preflop_overfold_cap: float = 0.15
    # The v1.8 rollout is limited by range_spec to validated common nodes.
    range_repair_enabled: bool = True
    # Additional 60BB limp / 2.5BB windows require whole-match confirmation.
    range_extension_enabled: bool = False
