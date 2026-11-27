"""ChipZen lifecycle adapter for the v2 runtime."""

import logging

from chipzen import Bot

from . import __version__
from .v2_runtime import V2Runtime


class V2HUBot(Bot):
    def __init__(self):
        self.runtime = None
        self.engine = None  # compatibility name for simple diagnostics
        self.hero_seat = 0
        self.dealer_seat = 0
        self.hand_number = 0
        self.last_match_summary = None
        self._seen_results = set()

    def _ensure_runtime(self):
        if self.runtime is None:
            self.runtime = V2Runtime()
            self.engine = self.runtime
        return self.runtime

    def on_match_start(self, message):
        config = message.get("game_config", {})
        runtime = self._ensure_runtime()
        runtime.reset(message.get("match_id", "offline"),
                      int(config.get("big_blind", 10)),
                      int(message.get("turn_timeout_ms", 2000)))
        self._seen_results.clear()
        self.last_match_summary = None
        for seat in message.get("seats", ()):
            if seat.get("is_self"):
                self.hero_seat = int(seat.get("seat", 0))
        logging.getLogger("hu-v2").info("RULES source=sdk_authority big_blind=%s",
                                        runtime.big_blind)

    def on_round_start(self, message):
        runtime = self._ensure_runtime()
        state = message.get("state", {})
        self.hand_number = int(state.get("hand_number", message.get("round_number", 0)))
        self.dealer_seat = int(state.get("dealer_seat", 0))
        stacks = state.get("stacks")
        if isinstance(stacks, list) and len(stacks) == 2:
            runtime.set_hand_start_stacks(self.hand_number, stacks)
        if state.get("big_blind"):
            runtime.big_blind = max(1, int(state["big_blind"]))

    def on_phase_change(self, message):
        return None

    def on_round_result(self, message):
        result = message.get("result", {})
        key = (message.get("round_id", ""), int(result.get("hand_number", self.hand_number)))
        if key in self._seen_results:
            return
        self._seen_results.add(key)
        runtime = self._ensure_runtime()
        runtime.match_counters["completed_hands"] += 1
        runtime.match_counters["observed_public_actions"] += len(result.get("action_history", ()))

    def on_reconnected(self, message):
        if self.runtime is None or self.runtime.match_id != message.get("match_id"):
            self.on_match_start(message)
        self.on_round_start(message)

    def on_match_end(self, results):
        if self.runtime is not None:
            self.last_match_summary = {
                "match": self.runtime.match_id,
                "version": __version__,
                "path": "v2",
                "decisions": self.runtime.decisions,
                "fallbacks": self.runtime.fallbacks,
                "counters": dict(self.runtime.match_counters),
            }
        self.runtime = None
        self.engine = None

    def decide(self, state):
        runtime = self._ensure_runtime()
        self.hero_seat = int(state.your_seat)
        self.dealer_seat = int(state.dealer_seat)
        self.hand_number = int(state.hand_number)
        return runtime.decide(state)

