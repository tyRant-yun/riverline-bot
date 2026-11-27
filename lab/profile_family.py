"""Predeclared, observation-only opponent archetypes for conditional HU runs."""
from __future__ import annotations

from dataclasses import asdict, dataclass
import hashlib
import json
from math import exp

from chipzen import Action

from hu_bot.poker import features, preflop_score


@dataclass(frozen=True)
class Archetype:
    name: str
    call_strength: float
    raise_strength: float
    river_raise_strength: float
    raise_probability: float
    river_bluff_probability: float
    size_fraction: float


# Values are intentionally mild; they are a fixed parameter design, not tuned on
# held-out hands. Neutral and extreme are controls for harness sensitivity.
ARCHETYPES = {
    "sticky_caller": Archetype("sticky_caller", .28, .90, .76, .06, .02, .55),
    "mild_overfolder": Archetype("mild_overfolder", .50, .78, .69, .08, .03, .60),
    "size_threshold_folder": Archetype("size_threshold_folder", .39, .80, .72, .07, .02, .42),
    "passive_rare_raiser": Archetype("passive_rare_raiser", .35, .94, .87, .025, .01, .50),
    "value_river_bettor": Archetype("value_river_bettor", .38, .82, .59, .30, .01, .72),
    "bluff_leaning_river_bettor": Archetype("bluff_leaning_river_bettor", .40, .74, .42, .12, .18, 1.10),
    "neutral": Archetype("neutral", .40, .82, .68, .10, .05, .60),
    "extreme_control": Archetype("extreme_control", .65, .97, .90, .01, .00, .80),
}


class ProfileFamily:
    """Scripted opponent using only its own cards, public board and action state."""

    def __init__(self, name: str, seed: int):
        if name not in ARCHETYPES:
            raise ValueError(f"unknown archetype: {name}")
        self.name, self.seed = name, seed

    def reset(self, _session_id: str = "") -> None:
        pass

    def _draw(self, state, stream):
        # Same private/public state has the same random draw in every paired arm,
        # even when another arm took a different number of earlier actions.
        line = tuple((e["seat"], e["action"], e.get("phase", "preflop"))
                     for e in state.action_history)
        identity = (self.seed, state.hand_number, state.phase,
                    tuple(map(str, state.hole_cards)), tuple(map(str, state.board)),
                    state.your_seat, line, stream)
        digest = hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).digest()
        return int.from_bytes(digest[:8], "big") / 2**64

    def decide(self, state):
        profile = ARCHETYPES[self.name]
        valid = set(state.valid_actions)
        hole = tuple(map(str, state.hole_cards))
        board = tuple(map(str, state.board))
        strength = features(hole, board).strength if board else preflop_score(hole)
        river = state.phase == "river"
        if state.to_call:
            threshold = profile.call_strength
            if self.name == "size_threshold_folder":
                threshold += min(.16, state.to_call / max(state.pot, 1) * .20)
            z = max(-30., min(30., (strength-threshold)/.10))
            call_probability = 1/(1+exp(-z))
            if self._draw(state, "call") >= call_probability:
                return Action.fold()
            raise_threshold = profile.river_raise_strength if river else profile.raise_strength
            if ("raise" in valid and strength >= raise_threshold and
                    self._draw(state, "facing_raise") < profile.raise_probability):
                return self._raise(state, profile.size_fraction)
            return Action.call()
        if "raise" in valid:
            threshold = profile.river_raise_strength if river else profile.raise_strength
            value = strength >= threshold and self._draw(state, "value_bet") < profile.raise_probability
            bluff = (river and strength < threshold and
                     self._draw(state, "bluff_bet") < profile.river_bluff_probability)
            if value or bluff:
                return self._raise(state, profile.size_fraction)
        return Action.check() if "check" in valid else Action.call()

    @staticmethod
    def _raise(state, fraction: float):
        amount = state.min_raise + round(state.pot * fraction)
        return Action.raise_to(min(state.max_raise, max(state.min_raise, amount)))


def parameter_manifest() -> dict[str, dict[str, float | str]]:
    return {name: asdict(profile) for name, profile in ARCHETYPES.items()}
