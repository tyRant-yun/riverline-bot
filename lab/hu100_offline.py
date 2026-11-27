"""Frozen one-decision EV sensitivity for proposed 72o/82o/83o/92o opens."""
import json
from pathlib import Path

from hu_bot.preflop_blueprint import PreflopBlueprint, small_tree_open_ev
from hu_bot.preflop_exploit import research_values


CONFIG = {"open_raise_to": 22, "big_blind": 10,
          "continuation_loss_bb": [2.2, 4.0, 8.0],
          "response_profiles": {"normal_heuristic": None, "overfold_hypothesis": .85,
                                "reverse": .10},
          "candidate_classes": ["72o", "82o", "83o", "92o"],
          "source": "assumed one-decision toy tree; not four-street rollout"}


def run(output):
    output = Path(output)
    if output.exists():
        raise FileExistsError(output)
    prior = PreflopBlueprint().bb_response_prior(22, 10)
    profiles = {**CONFIG["response_profiles"], "normal_heuristic": prior["fold"]}
    sensitivity = {name: {str(loss): small_tree_open_ev(fold, 2.2,
                                                        continuation_loss_bb=loss)
                          for loss in CONFIG["continuation_loss_bb"]}
                   for name, fold in profiles.items()}
    receipt = {"config": CONFIG, "fold_rates": profiles,
               "ev_bb_vs_fold": sensitivity,
               "research_values": [v.__dict__ for v in research_values(prior["fold"],
                                                                          open_to_bb=2.2)],
               "four_street_ev_available": False,
               "eligible_for_online_value_gate": False}
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(receipt, indent=2)+"\n", encoding="utf-8")
    return receipt


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    print(json.dumps(run(args.output)["ev_bb_vs_fold"], indent=2))
