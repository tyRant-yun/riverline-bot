"""Paired assumed-schedule checks with match-local learning enabled."""
from pathlib import Path
import json

from lab.hu100_matches import MatchEvaluator, MatchRules


EXPERIMENTS = (
    ("extension_exact25_tight", "range_extension", "exact_25_tight", 600, (190501, 190502)),
    ("extension_exact25_call", "range_extension", "exact_25_call", 600, (190503, 190504)),
    ("extension_exact25_aggressive", "range_extension", "exact_25_aggressive", 600,
     (190505, 190506)),
    ("overfold_normal", "overfold_research", "normal", 1000, (190507,)),
    ("overfold_target", "overfold_research", "exact_open_overfolder", 1000,
     (190508,)),
    ("overfold_reverse", "overfold_research", "aggressive", 1000, (190509,)),
)


def run(output_dir, fixed=False):
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=False)
    summary = []
    experiments = EXPERIMENTS[:3] + EXPERIMENTS[4:5] if fixed else EXPERIMENTS
    for name, comparison, opponent, chips, seeds in experiments:
        rules = MatchRules(starting_chips=chips, ramp_every_hands=None if fixed else 25,
                           max_hands_safety=100, source="assumed_for_research")
        result = MatchEvaluator(rules, seeds, opponent=opponent,
                                comparison=comparison,
                                within_match_learning=True).evaluate()
        (output / f"{name}.json").write_text(json.dumps(result, separators=(",", ":")) + "\n",
                                              encoding="utf-8")
        pairs = result["pairs"]
        summary.append({"name": name, "opponent": opponent,
                        "comparison": comparison, "seeds": seeds,
                        "match_count": result["match_count"],
                        "paired_score_difference_mean": result[
                            "paired_score_difference_mean"],
                        "paired_normal_95_ci": result["paired_normal_95_ci"],
                        "censored_matches": result["censored_matches"],
                        "candidate_extension_interventions": sum(
                            match["extension_interventions"] for pair in pairs
                            for match in pair["candidate"]),
                        "candidate_exploit_interventions": sum(
                            match["exploit_interventions"] for pair in pairs
                            for match in pair["candidate"]),
                        "candidate_exploit_reached": sum(
                            match["exploit_reached"] for pair in pairs
                            for match in pair["candidate"]),
                        "baseline_fallbacks": sum(match["fallbacks"] for pair in pairs
                                                  for match in pair["baseline"]),
                        "candidate_fallbacks": sum(match["fallbacks"] for pair in pairs
                                                   for match in pair["candidate"]),
                        "candidate_max_ms": max(match["max_decision_ms"] for pair in pairs
                                                for match in pair["candidate"]),
                        "candidate_final_chips": [match["final_stacks"][match["hero_seat"]]
                                                  for pair in pairs for match in pair["candidate"]]})
    (output / "summary.json").write_text(json.dumps({"version": "1.9.0",
         "schedule": "fixed_blind" if fixed else "ramp_25_double",
         "rules_source": "assumed_for_research",
         "independent_unit": "paired seed with swapped seats and separate match memories",
         "experiments": summary,
         "limitation": "Small exploratory scripted matches; zero-variance intervals do not establish equivalence."},
         indent=2) + "\n", encoding="utf-8")
    return summary


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("output_dir", type=Path)
    parser.add_argument("--fixed", action="store_true")
    args = parser.parse_args()
    print(json.dumps(run(args.output_dir, args.fixed), indent=2))
