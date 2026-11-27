"""Deterministic theory probes of v1.2.2; not empirical win-rate evaluation."""
import argparse
from dataclasses import replace
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/"submission"))
from hu_bot.core import normalize, Policy  # noqa: E402
from hu_bot.memory import OpponentModel  # noqa: E402
from hu_bot.poker import features  # noqa: E402
from hu_bot.preflop import DATA, lookup  # noqa: E402
from hu_bot.ranges import EquityResult  # noqa: E402
from hu_bot.strategy import postflop_policy, exploit  # noqa: E402
from lab.scenarios import spot, entry, SRP  # noqa: E402


def audit():
    royal = normalize(spot("7h 2d", "As Ks Qs Js Ts", pot=650, to_call=600,
        opponent_stacks=[375], valid_actions=["fold", "call", "raise"],
        min_raise=975, max_raise=975, action_history=SRP+[entry(1, "raise", 600, "river")]))
    board_eq = EquityResult(.5, 990, 0., True, True, "guaranteed_shared_royal")
    board_policy = postflop_policy(royal, features(royal.hero_cards, royal.board), board_eq)
    checkdown_equity = EquityResult(.45, 900, .0164, False, False, "controlled_equity_input")
    allin = normalize(spot("Ah 2d", "Js 9c 4h", pot=150, to_call=100,
        your_stack=100, opponent_stacks=[0], valid_actions=["fold", "call"],
        min_raise=0, max_raise=0,
        action_history=SRP+[entry(1, "raise", 100, "flop")]))
    positions = {}
    for ip in (True, False):
        s = replace(allin, in_position=ip)
        positions["IP" if ip else "OOP"] = postflop_policy(s, features(s.hero_cards, s.board), checkdown_equity).weights
    # Scoring policy frequencies does not require drawing the action RNG.
    # These are conditional known-equity probes, not measured equity of this hand.
    jam_ev = .45*(150+100)-100
    frequencies = {}
    for depth in DATA["depth_anchors_bb"]:
        total = dict.fromkeys(DATA["actions"], 0.)
        for hand in DATA["classes"]:
            combos = 6 if len(hand) == 2 else 4 if hand.endswith("s") else 12
            for action, p in lookup("BTN_OPEN", depth, 2, hand).weights.items():
                total[action] += combos*p/1326
        frequencies[str(depth)] = {a: round(p, 6) for a, p in total.items() if p}
    # Existing generic exploit uses aggregate observed folds, without recording
    # the action size or its conditional continuation EV in the adjustment.
    bluff = normalize(spot("7h 2d", "As Kc Qd 9h 4s"))
    model = OpponentModel()
    for index in range(200):
        model.stat("fold_to_river_bet", .4).observe(index < 108)
    baseline = Policy({"CHECK": 1})
    adapted, detail = exploit(baseline, bluff, features(bluff.hero_cards, bluff.board), model, .15)
    return {"schema": "hu-theory-audit-v1", "source_version": "1.2.2",
        "runtime_sha256": {p.relative_to(ROOT).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted((ROOT/"submission").rglob("*"))
            if p.is_file() and "__pycache__" not in p.parts and p.suffix != ".pyc"},
        "shared_royal": {"pot_including_bet": royal.pot, "call": royal.to_call,
            "equity": .5, "baseline_policy": board_policy.weights,
            "call_ev": (royal.pot-royal.to_call)/2,
            "policy_ev_loss_vs_call": board_policy.weights.get("FOLD", 0)*(royal.pot-royal.to_call)/2},
        "terminal_allin": {"controlled_equity_not_actual_hand_estimate": .45,
            "pot": 150, "call": 100, "call_ev": jam_ev, "policies": positions,
            "policy_ev_loss_vs_call": {k: v.get("FOLD", 0)*jam_ev for k, v in positions.items()}},
        "btn_combo_weighted_frequencies": frequencies,
        "below_chart_minimum": {str(d): lookup("BTN_OPEN", d, 2, "32o").weights for d in (2, 4, 5)},
        "generic_exploit_example": {"policy": adapted.weights, "detail": detail,
            "limitation": "Only an action-frequency diagnostic. Aggregate folds do not establish size-conditioned EV."},
        "limitations": "Constructed policy probes. No full-game exploitability, equilibrium quality or tournament win-rate estimate."}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    target = Path(args.output)
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("x", encoding="utf-8") as out:
        json.dump(audit(), out, ensure_ascii=False, indent=2)
    print(target)
