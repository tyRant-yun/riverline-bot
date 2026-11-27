"""RangeSpec coverage, runtime gating, and executable bet sizes."""
from dataclasses import replace

import pytest

from hu_bot.config import Config
from hu_bot.core import normalize
from hu_bot.preflop import DATA, preflop_policy
from hu_bot.range_spec import _node, _spec, candidate_policy
from hu_bot.strategy import legalize, sizing
from lab.range_rollout import CLASS_COMBOS, CONFIG
from lab.scenarios import BLINDS, entry, spot


def state(context, depth, price, cards):
    chips = depth*10
    events = list(BLINDS)
    bets = [5, 10]
    if context == "BB_VS_LIMP":
        events.append(entry(0, "call", 5))
        bets[0] = 10
    elif context == "BB_VS_OPEN":
        amount = round(price*10)
        events.append(entry(0, "raise", amount))
        bets[0] = amount
    elif context == "BTN_VS_ISO":
        amount = round(price*10)
        events += [entry(0, "call", 5), entry(1, "raise", amount)]
        bets = [10, amount]
    elif context in {"BTN_VS_3BET", "BB_VS_4BET"}:
        opened = round(price*10)
        three = round(opened*3.5)
        events += [entry(0, "raise", opened), entry(1, "raise", three)]
        bets = [opened, three]
        if context == "BB_VS_4BET":
            four = round(three*2.25)
            events.append(entry(0, "raise", four))
            bets[0] = four
    elif context == "BB_VS_LIMP_RERAISE":
        iso = 35
        four = round(iso*2.25)
        events += [entry(0, "call", 5), entry(1, "raise", iso),
                   entry(0, "raise", four)]
        bets = [four, iso]
    elif context != "BTN_FIRST_IN":
        raise ValueError(context)
    hero = 0 if context.startswith("BTN") else 1
    other = 1-hero
    owed = max(0, bets[other]-bets[hero])
    raw = spot(" ".join(cards), your_seat=hero, pot=sum(bets),
               your_stack=chips-bets[hero], opponent_stacks=[chips-bets[other]],
               to_call=owed, min_raise=max(bets)+10, max_raise=chips,
               valid_actions=["fold", "call", "raise"] if owed else ["check", "raise"],
               action_history=events)
    return normalize(raw, 10)


def test_full_grid_and_bounded_default_enabled():
    spec = _spec()
    assert len(spec["classes"]) == len(DATA["classes"]) == 169
    assert Config().range_repair_enabled is True
    assert Config().preflop_overfold_enabled is False
    for context, prices in CONFIG["scenarios"].items():
        for depth in CONFIG["depths_bb"]:
            for price in prices:
                rows = spec["tables"][f"{context}:{depth}:{price}"]
                assert len(rows) == 169
                for klass, row in zip(DATA["classes"], rows):
                    assert len(row) == len(CONFIG["actions"][context])
                    assert sum(row) == pytest.approx(1)
                    s = state(context, depth, price, CLASS_COMBOS[klass][0])
                    policy = candidate_policy(s)
                    enabled = (context == "BTN_FIRST_IN" and depth == 60 or
                               context == "BB_VS_LIMP" and depth == 100 or
                               context == "BB_VS_OPEN" and depth == 60 and price <= 2.25)
                    assert (policy is not None) == enabled
                    if policy is not None:
                        assert sum(policy.weights.values()) == pytest.approx(1)
                        for label in policy.weights:
                            action, fallback = legalize(label, s)
                            assert fallback is None
                            assert action.action in s.valid_actions
                            if action.action == "raise":
                                assert s.min_raise <= action.amount <= s.max_raise
                    assert preflop_policy(s).weights != {}


def test_isolation_price_depth_and_ante_gates():
    cards = CLASS_COMBOS["Q8o"][0]
    s = state("BB_VS_OPEN", 60, 2.25, cards)
    assert candidate_policy(s) is not None
    assert candidate_policy(replace(s, effective_stack_bb=65)) is not None
    assert candidate_policy(replace(s, effective_stack_bb=55)) is not None
    assert candidate_policy(replace(s, effective_stack_bb=49)) is None
    assert candidate_policy(replace(s, effective_stack_bb=70)) is None
    two = state("BB_VS_OPEN", 60, 2, cards)
    assert candidate_policy(replace(two, effective_stack_bb=50)) is not None
    assert candidate_policy(replace(two, effective_stack_bb=70)) is not None
    assert candidate_policy(replace(two, effective_stack_bb=75)) is None
    assert candidate_policy(replace(s, num_players=3)) is None
    assert candidate_policy(replace(s, action_history=s.action_history+
                                    (entry(0, "post_ante", 1),))) is None
    assert candidate_policy(replace(s, villain_bet=100,
                                    action_history=s.action_history[:-1]+
                                    (entry(0, "raise", 100),))) is None
    assert preflop_policy(s).weights == preflop_policy(s, use_range_spec=False).weights


def test_actual_reraise_price_is_keyed_and_unvalidated_nodes_fall_back():
    three = state("BTN_VS_3BET", 100, 2, CLASS_COMBOS["98s"][0])
    large_three = replace(three, villain_bet=300, to_call=280,
                          action_history=three.action_history[:-1]+
                          (entry(1, "raise", 300),))
    assert _node(three) == ("BTN_VS_3BET", 7)
    assert _node(large_three) == ("BTN_VS_3BET", 30)
    assert candidate_policy(three) is candidate_policy(large_three) is None
    assert preflop_policy(large_three, use_range_spec=True).weights == preflop_policy(
        large_three, use_range_spec=False).weights

    four = state("BB_VS_4BET", 100, 2, CLASS_COMBOS["32o"][0])
    large_four = replace(four, villain_bet=500, to_call=430,
                         action_history=four.action_history[:-1]+
                         (entry(0, "raise", 500),))
    assert _node(four)[1] == pytest.approx(four.villain_bet/four.big_blind)
    assert _node(large_four) == ("BB_VS_4BET", 50)
    assert candidate_policy(four) is candidate_policy(large_four) is None
    assert preflop_policy(large_four, use_range_spec=True).weights == preflop_policy(
        large_four, use_range_spec=False).weights


def test_default_range_changes_multiple_classes_in_promoted_nodes():
    for context, depth, price in (("BTN_FIRST_IN", 60, 0),
                                  ("BB_VS_LIMP", 100, 1),
                                  ("BB_VS_OPEN", 60, 2)):
        changed = sum(preflop_policy(state(context, depth, price, cards), True).weights !=
                      preflop_policy(state(context, depth, price, cards), False).weights
                      for cards in (CLASS_COMBOS[k][0] for k in DATA["classes"]))
        assert changed >= 20


def test_new_raise_labels_execute_actual_prices():
    btn = state("BTN_FIRST_IN", 100, 0, CLASS_COMBOS["AA"][0])
    assert sizing("OPEN_2", btn) == 20
    assert sizing("OPEN_225", btn) == 22
    assert sizing("OPEN_25", btn) == 25
    bb = state("BB_VS_LIMP", 100, 1, CLASS_COMBOS["AA"][0])
    assert [sizing(label, bb) for label in ("ISO_3", "ISO_35", "ISO_4", "ISO_5")] == [30, 35, 40, 50]
    opened = state("BB_VS_OPEN", 100, 2.5, CLASS_COMBOS["AA"][0])
    assert [sizing(label, opened) for label in ("THREEBET_3", "THREEBET_33",
                                                      "THREEBET_35", "THREEBET_4")] == [75, 82, 88, 100]
    assert legalize("THREEBET_35", opened)[0].to_dict() == {"action": "raise", "amount": 88}


@pytest.mark.parametrize("depth", [30, 20, 12, 8])
@pytest.mark.parametrize("context,price", [("BTN_FIRST_IN", 0),
                                           ("BB_VS_OPEN", 2.5)])
def test_short_stack_jam_related_nodes_keep_legal_baseline(depth, context, price):
    for klass in DATA["classes"]:
        s = state(context, depth, price, CLASS_COMBOS[klass][0])
        assert candidate_policy(s) is None
        policy = preflop_policy(s, use_range_spec=True)
        assert policy.weights == preflop_policy(s, use_range_spec=False).weights
        for label, weight in policy.weights.items():
            if weight <= 0:
                continue
            action, _ = legalize(label, s)
            assert action.action in s.valid_actions
            if action.action == "raise":
                assert s.min_raise <= action.amount <= s.max_raise
