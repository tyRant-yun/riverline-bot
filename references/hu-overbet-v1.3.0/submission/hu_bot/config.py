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
