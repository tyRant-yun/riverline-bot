"""Chipzen uploaded-container entry point. Network is used only by the SDK transport."""
import asyncio
import logging
import os

from chipzen import Bot, run_bot

from hu_bot import __version__
from hu_bot.accounting import MatchEventDeduper, RuleSnapshot


class HUBot(Bot):
    def __init__(self):
        # Keep the transport hello path light. The range tables and combo
        # posterior are initialized only after the executor sends match_start.
        self.engine = None
        self.hero_seat = 0
        self.dealer_seat = 0
        self.hand_number = 0
        self.round_id = ""
        self.board = []
        self.last_match_summary = None
        from hu_bot.profiles import ProfileCache
        self.profiles = ProfileCache()
        self.profile_key = None
        self.last_result_hand_number = 0
        self.result_deduper = MatchEventDeduper()

    def _ensure_engine(self):
        if self.engine is None:
            from hu_bot.engine import Engine
            from hu_bot.config import Config
            # Uploaded matches keep only bounded, match-local learning state.
            # Full traces and replay payloads remain available to the offline lab.
            self.engine = Engine(Config(trace=False, retain_diagnostics=False,
                                        log_byte_budget=8192))
        return self.engine

    def on_match_start(self, message):
        self._ensure_engine()
        if self.profile_key is not None:
            self.profiles.save(self.profile_key, self.engine.opponent)
        self.last_match_summary = None
        self.round_id, self.hand_number, self.board = "", 0, []
        self.last_result_hand_number = 0
        self.result_deduper.reset(message.get("match_id", "offline"))
        config = message.get("game_config", {})
        self.engine.reset(message.get("match_id", "offline"),
                          int(config.get("big_blind", 10)),
                          int(message.get("turn_timeout_ms", 2000)))
        self.engine.set_rule_snapshot(RuleSnapshot.from_config(
            message.get("match_id", "offline"), config,
            source="platform_verified"))
        self.profile_key = self.profiles.key(message)
        self.profiles.apply_weak_prior(self.profile_key, self.engine.opponent)
        logging.getLogger("hu-v1").info("RULES %s", self.engine.rules)
        for seat in message.get("seats", []):
            if seat.get("is_self"):
                self.hero_seat = int(seat["seat"])

    def on_round_start(self, message):
        state = message.get("state", {})
        self.round_id = message.get("round_id", "")
        self.hand_number = int(state.get("hand_number", message.get("round_number", 0)))
        self.dealer_seat = int(state.get("dealer_seat", 0))
        self.board = []
        if state.get("big_blind"):
            engine = self._ensure_engine()
            observed_bb = int(state["big_blind"])
            engine.big_blind = observed_bb
            engine.set_rule_snapshot(engine.rule_snapshot.with_observed_big_blind(observed_bb))
        if isinstance(state.get("stacks"), list) and len(state["stacks"]) == 2:
            engine = self._ensure_engine()
            engine.set_hand_start_stacks(self.hand_number, state["stacks"])
            engine.hand_depths[self.hand_number] = min(map(int, state["stacks"])) / engine.big_blind

    def on_phase_change(self, message):
        self.board = list(message.get("state", {}).get("board", []))

    def on_round_result(self, message):
        result = message.get("result", {})
        engine = self._ensure_engine()
        result_hand = int(result.get("hand_number", self.hand_number))
        if not self.result_deduper.first_result(message.get("round_id") or self.round_id,
                                                result_hand):
            return
        if result_hand > self.last_result_hand_number:
            engine.match_counters["server_timeout_actions"] += sum(
                bool(e.get("is_timeout")) and e.get("seat") == self.hero_seat
                for e in result.get("action_history", ()))
            self.last_result_hand_number = result_hand
        engine.opponent.observe_hand(message.get("round_id") or self.round_id or self.hand_number,
            result, self.hero_seat, self.dealer_seat, result.get("board", self.board),
            big_blind=engine.big_blind, depth_bb=engine.hand_depths.pop(self.hand_number, None))

    def on_match_end(self, results):
        if self.engine is not None:
            from hu_bot.preflop import TABLE_SHA256
            from hu_bot.range_spec import RANGE_SPEC_SHA256
            self.profiles.save(self.profile_key, self.engine.opponent)
            self.last_match_summary = {"match": self.engine.match_id,
                "version": __version__,
                "table_sha256": TABLE_SHA256,
                "range_spec_sha256": RANGE_SPEC_SHA256,
                "hands": self.hand_number, "decisions": self.engine.decisions,
                "fallbacks": self.engine.fallbacks, "last_big_blind": self.engine.big_blind,
                "counters": dict(self.engine.match_counters)}
            logging.getLogger("hu-v1").info("MATCH_SUMMARY %s", self.last_match_summary)
        self.engine = None
        self.profile_key = None
        self.round_id, self.hand_number, self.board = "", 0, []

    def on_reconnected(self, message):
        # A same-match reconnect must not erase accumulated observations.
        if self.engine is None or self.engine.match_id != message.get("match_id"):
            self.on_match_start(message)
        elif message.get("turn_timeout_ms"):
            self.engine.timeout_ms = int(message["turn_timeout_ms"])
        for seat in message.get("seats", []):
            if seat.get("is_self"):
                self.hero_seat = int(seat["seat"])
        self.on_round_start(message)
        self.board = list(message.get("state", {}).get("board", []))

    def decide(self, state):
        # SDK validation may call decide without a match_start fixture.
        engine = self._ensure_engine()
        self.hero_seat, self.dealer_seat = state.your_seat, state.dealer_seat
        self.hand_number = state.hand_number
        self.board = list(map(str, state.board))
        return engine.decide(state)


def main():
    logging.basicConfig(level=logging.WARNING, format="%(message)s")
    logging.getLogger("hu-v1").setLevel(logging.INFO)
    url = os.environ.get("CHIPZEN_WS_URL")
    if not url:
        raise SystemExit("CHIPZEN_WS_URL is required (provided by Chipzen executor)")
    from hu_bot.v2_bot import V2HUBot
    asyncio.run(run_bot(url=url, bot=V2HUBot(), token=os.environ.get("CHIPZEN_TOKEN") or None,
                       ticket=os.environ.get("CHIPZEN_TICKET") or None,
                       match_id=os.environ.get("CHIPZEN_MATCH_ID") or None,
                       client_name="hu-bot-v2", client_version=__version__))


if __name__ == "__main__":
    main()
