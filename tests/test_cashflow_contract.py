"""Research harness cash flow; platform settlement still needs an official replay."""
from chipzen import Action

from hu_bot.poker import DECK
from lab.match import play_hand
from lab.hu100_matches import MatchRules
import pytest


class Passive:
    def decide(self, state):
        if "check" in state.valid_actions:
            return Action.check()
        return Action.call()


def test_unequal_blind_all_in_returns_uncalled_excess_and_conserves_chips():
    players = [Passive(), Passive()]
    deck = ["As", "Ah", "2c", "3d", "Kd", "Qc", "Js", "9h", "4s"]
    deck += [card for card in DECK if card not in deck]
    net, _, result = play_hand(players, deck, 1, button=0, bb=10,
                               starting_stacks=[5, 100])
    assert result["uncalled_refunds"] == [0, 5]
    assert result["pot"] == 10
    assert result["payouts"] == [{"seat": 0, "amount": 10},
                                  {"seat": 1, "amount": 0}]
    assert result["stacks"] == [10, 95]
    assert net == [5, -5]
    assert sum(result["stacks"]) == 105


def test_regular_raise_to_and_call_use_total_bet_and_include_current_bet_in_pot():
    class RaiseOnce(Passive):
        def decide(self, state):
            if state.phase == "preflop" and state.your_seat == state.dealer_seat:
                return Action.raise_to(25)
            return super().decide(state)

    deck = list(DECK)
    _, _, result = play_hand([RaiseOnce(), Passive()], deck, 1,
                             button=0, bb=10, starting_stacks=[100, 100])
    history = result["action_history"]
    assert history[2]["action"] == "raise" and history[2]["amount"] == 25
    assert history[3]["action"] == "call"
    assert result["uncalled_refunds"] == [0, 0]
    assert result["pot"] == 50
    assert sum(result["stacks"]) == 200


def test_schedule_cannot_claim_platform_verification_without_receipt():
    assert MatchRules().source == "assumed_for_research"
    with pytest.raises(ValueError):
        MatchRules(source="platform_verified")
    assert MatchRules(source="platform_verified",
                      evidence_uri="wire://local-match-config").source == "platform_verified"
