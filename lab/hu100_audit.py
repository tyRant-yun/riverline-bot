"""Freeze 1326-combo 100BB/no-ante heuristic baseline and actual chip sizes."""
import csv
from dataclasses import asdict, replace
import hashlib
import json
from itertools import combinations
from pathlib import Path

from chipzen.models import Card

from hu_bot import __version__
from hu_bot.core import normalize
from hu_bot.poker import DECK, hand_class
from hu_bot.preflop_blueprint import PreflopBlueprint, RuleSpec
from lab.scenarios import BLINDS, entry, spot


def state_for(node, cards):
    hole = [Card.from_str(card) for card in cards]
    if node == "BTN_FIRST_IN":
        raw = spot()
    elif node == "BB_VS_LIMP":
        raw = spot(your_seat=1, pot=20, your_stack=990,
                   opponent_stacks=[990], to_call=0, min_raise=20,
                   valid_actions=["check", "raise"],
                   action_history=BLINDS+[entry(0, "call", 5)])
    elif node == "BB_VS_OPEN":
        raw = spot(your_seat=1, pot=32, your_stack=990,
                   opponent_stacks=[978], to_call=12, min_raise=34,
                   action_history=BLINDS+[entry(0, "raise", 22)])
    else:
        raise ValueError(node)
    return normalize(replace(raw, hole_cards=hole), 10)


def conditional_weights(combos, first_policies):
    """Mirror-chart BTN reach weights with exact blockers, as a model assumption."""
    result = {"BB_VS_LIMP": [], "BB_VS_OPEN": []}
    opener = [(set(c), p.get("CALL", 0), p.get("RAISE_SMALL", 0))
              for c, p in zip(combos, first_policies)]
    for bb_cards in combos:
        blocked = set(bb_cards)
        limp = open_ = 0.0
        for btn_cards, limp_probability, open_probability in opener:
            if blocked.isdisjoint(btn_cards):
                limp += limp_probability
                open_ += open_probability
        result["BB_VS_LIMP"].append(limp)
        result["BB_VS_OPEN"].append(open_)
    for values in result.values():
        total = sum(values)
        values[:] = [value/total for value in values]
    return result


def audit(output):
    output = Path(output)
    output.mkdir(parents=True, exist_ok=False)
    spec = RuleSpec()
    combos = list(combinations(DECK, 2))
    blueprint = PreflopBlueprint()
    first = [blueprint.policy(state_for("BTN_FIRST_IN", cards)).weights for cards in combos]
    conditional = conditional_weights(combos, first)
    summary = {"version": __version__, "table_sha256": blueprint.table_sha256,
               "source": blueprint.source, "rules": asdict(spec),
               "node_order": ["BTN_FIRST_IN", "BB_VS_LIMP", "BB_VS_OPEN"],
               "combos_per_node": 1326, "arrival_model": "mirror frozen BTN chart with blockers; not observed opponent range",
               "ev": "unavailable: no matching four-street action-value solution",
               "nodes": {}}
    rows = []
    for node in summary["node_order"]:
        aggregate_uniform, aggregate_conditional = {}, {}
        for index, cards in enumerate(combos):
            state = state_for(node, cards)
            base = blueprint.policy(state)
            executable = blueprint.legal_policy(state)
            uniform = 1/1326
            conditional_weight = (uniform if node == "BTN_FIRST_IN"
                                  else conditional[node][index])
            for (action, amount), probability in executable.items():
                label = f"{action}:{amount}" if action == "raise" else action
                aggregate_uniform[label] = aggregate_uniform.get(label, 0)+uniform*probability
                aggregate_conditional[label] = (aggregate_conditional.get(label, 0)
                                                +conditional_weight*probability)
            rows.append({"node": node, "cards": " ".join(cards),
                         "class": hand_class(cards), "prior_weight": uniform,
                         "conditional_weight": conditional_weight,
                         "abstract_policy": json.dumps(base.weights, sort_keys=True),
                         "executable_policy": json.dumps(
                             {f"{a}:{n}" if a == "raise" else a: p
                              for (a, n), p in executable.items()}, sort_keys=True),
                         "action_ev": "unavailable"})
        summary["nodes"][node] = {"uniform_1326": aggregate_uniform,
                                  "conditional_on_arrival_model": aggregate_conditional,
                                  "denominator": 1326}
    csv_path = output/"combo-policy.csv"
    with csv_path.open("x", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    summary["combo_csv_sha256"] = hashlib.sha256(csv_path.read_bytes()).hexdigest()
    (output/"baseline-audit.json").write_text(json.dumps(summary, indent=2)+"\n", encoding="utf-8")
    return summary


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    print(json.dumps(audit(args.output)["nodes"], indent=2))
