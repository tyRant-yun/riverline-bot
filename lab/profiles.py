"""Original fixed-style probes. These are deliberately simple, not strength oracles."""
from chipzen import Action

from hu_bot.poker import features, preflop_score
from lab.match import Baseline

STYLES = ("call", "tight", "aggressive", "random", "overfolder", "cbet_giveup", "size_tell")


class Profile(Baseline):
    def decide(self, state):
        if self.name in {"call", "tight", "aggressive", "random"}:
            return super().decide(state)
        hole, board = tuple(map(str, state.hole_cards)), tuple(map(str, state.board))
        strength = features(hole, board).strength if board else preflop_score(hole)
        if self.name == "overfolder":
            if state.to_call and strength < .64:
                return Action.fold()
            aggressive = strength > .80
            fraction = .66
        elif self.name == "cbet_giveup":
            aggressive = (state.phase == "preflop" and strength > .38 or
                          state.phase == "flop" or strength > .82)
            if state.to_call and state.phase in {"turn", "river"} and strength < .62:
                return Action.fold()
            fraction = .5
        elif self.name == "size_tell":
            aggressive = strength > .7 or self.rng.random() < .25
            fraction = 1. if strength > .7 else .25
            if state.to_call and strength < .42:
                return Action.fold()
        else:
            raise ValueError(self.name)
        if aggressive and "raise" in state.valid_actions:
            # A legal, deliberately fixed sizing rule; no future cards or hidden hands.
            return Action.raise_to(min(state.max_raise, max(state.min_raise,
                                    state.min_raise+round(state.pot*fraction))))
        return Action.check() if "check" in state.valid_actions else Action.call()
