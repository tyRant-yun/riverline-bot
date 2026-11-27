"""Preregistered small fixed-stack experiment plus known-truth river probes."""
import argparse
from dataclasses import asdict, replace
import importlib.util
from importlib.metadata import version
import json
from pathlib import Path
import random
import sys
from time import perf_counter

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/"submission"))
from hu_bot.config import Config  # noqa: E402
from hu_bot.engine import Engine  # noqa: E402
from hu_bot.poker import DECK  # noqa: E402
from lab.checkpoints import _sha, _write_new, _files  # noqa: E402
from lab.match import play_hand  # noqa: E402
from lab.scenarios import spot, entry, SRP  # noqa: E402
from treys import Card, Evaluator  # noqa: E402


def frozen_factory(path):
    path = Path(path).resolve()
    # The frozen terminal module uses an absolute core import. It is safe to
    # share this utility only when its exact bytes agree with the frozen file.
    if (path/"submission/hu_bot/core.py").read_bytes() != (ROOT/"submission/hu_bot/core.py").read_bytes():
        raise RuntimeError("frozen opponent's shared core utility differs")
    package = "overbet_frozen_v123"
    spec = importlib.util.spec_from_file_location(package, path/"submission/hu_bot/__init__.py",
                  submodule_search_locations=[str(path/"submission/hu_bot")])
    module = importlib.util.module_from_spec(spec)
    sys.modules[package] = module
    spec.loader.exec_module(module)
    from importlib import import_module
    engine = import_module(package+".engine").Engine
    config = import_module(package+".config").Config
    return lambda: engine(config(trace=False))


def configurations():
    base = Config(trace=False)
    return {"control": replace(base, polar_river_ranges=False, robust_river_defense=False),
            "candidate": replace(base, polar_river_ranges=True, robust_river_defense=True)}


def oracle_probes(configs):
    """Truth is used by the scorer only. All worlds expose the same visible state.

    Explicit losing/winning villain combos define three scoring worlds for
    four hero hand classes. An independent evaluator checks both payoff rows.
    Villain is all-in, so only fold/call are legal and both payoff rows are exact.
    """
    rows = []
    evaluator = Evaluator()
    board = "As 8c 4d 2h 9s"
    cases = [("weak_top_pair", "Ah 7d", "8d 8h"),
             ("second_pair", "8d 7d", "9h 9d"),
             ("king_high", "Kd Qd", "8d 8h"),
             ("set", "9h 9d", "Ac Ad")]
    for ratio in (1.5, 2., 4., 8.):
        bet = int(round(50*ratio))
        for case, hole, value in cases:
            ranks = [evaluator.evaluate([Card.new(c) for c in board.split()],
                     [Card.new(c) for c in cards.split()]) for cards in (hole, value, "7c 6c")]
            assert ranks[1] < ranks[0] < ranks[2], "independent truth payoff mismatch"
            for mode, config in configs.items():
                bot = Engine(config)
                bot.reset(f"oracle-{ratio}-{case}")
                state = spot(hole, board, pot=50+bet,
                    your_stack=975, opponent_stacks=[0], to_call=bet, min_raise=0, max_raise=0,
                    valid_actions=["fold", "call"], action_history=SRP+[entry(1, "all_in", bet, "river")])
                bot.decide(state)
                trace = bot.last_trace
                if (trace.get("attribution", {}).get("fallback_class") == "engine_exception"
                        or trace["action"]["action"] not in state.valid_actions):
                    raise RuntimeError(f"oracle fallback: {trace}")
                # ALL_IN/RAISE are legally mapped to CALL against an all-in opponent.
                p_call = sum(p for label, p in trace["policy"].items() if label != "FOLD")
                for world, q in (("value_heavy", .10), ("price_indifferent", bet/(50+2*bet)),
                                 ("bluff_heavy", .70)):
                    call_ev = q*(50+2*bet)-bet
                    rows.append({"case": case, "hole": hole, "board": board,
                        "truth_value_combo": value, "truth_bluff_combo": "7c 6c",
                        "ratio": bet/50, "mode": mode, "world": world,
                        "truth_hero_win_probability": q, "call_probability": p_call,
                        "call_ev": call_ev, "policy_ev": p_call*call_ev,
                        "regret": max(0, call_ev)-p_call*call_ev,
                        "range": trace.get("villain_range"), "defense": trace.get("river_defense"),
                        "legal_remap": trace.get("fallback"),
                        "sampled_action": trace["action"], "elapsed_ms": trace["elapsed_ms"]})
    return rows


def run(output, frozen_path, pairs=4, hands=20, seed=923013):
    if not 1 <= pairs <= 4 or not 1 <= hands <= 20:
        raise ValueError("This smoke experiment is capped at 4 pairs x 20 hands per seat and arm")
    output, frozen_path = Path(output), Path(frozen_path)
    configs = configurations()
    manifest = {"type": "overbet-v13-small-paired-exploration", "pairs": pairs, "hands_per_rotation": hands,
        "seed": seed, "seats": [0, 1], "configs": {k: asdict(v) for k, v in configs.items()},
        "rules": {"stack": 1000, "bb": 10, "ante": 0, "reset_each_hand": True},
        "runtime_files": _files(ROOT, "submission"), "lab_files": _files(ROOT, "lab"),
        "opponent_files": _files(frozen_path, "submission"), "python": sys.version,
        "dependencies": {name: version(name) for name in ("chipzen-bot", "websockets", "treys")},
        "decision": "exploratory only; no significance claim or automatic promotion",
        "oracle_stack_contract": "Hero starts 1000; all-in villain starts 25+B. Terminal probes vary effective depth along with bet size; they are not a pure size-isolation or fixed-100BB strength test.",
        "oracle_truth": "Four hero hands each beat 7c6c and lose to declared value combo; treys-verified; q=.10, call-price, .70; truth never sent to Engine"}
    fingerprint = _sha(manifest)
    _write_new(output/"protocol.json", {**manifest, "fingerprint": fingerprint})
    factory = frozen_factory(frozen_path)
    start, pair_rows = perf_counter(), []
    try:
        probes = oracle_probes(configs)
        for index in range(pairs):
            rng = random.Random(seed+index*10007)
            decks = [rng.sample(DECK, 52) for _ in range(hands)]
            pair = {"pair": index, "seed": seed+index*10007, "arms": {}}
            for mode, config in configs.items():
                scores, interventions, fallbacks, exceptions, decisions = [], 0, 0, 0, 0
                for seat in (0, 1):
                    hero, villain = Engine(config), factory()
                    hero.reset(f"pair-{index}-seat-{seat}")
                    villain.reset(f"opponent-{index}-seat-{seat}")
                    players = [villain, villain]
                    players[seat] = hero
                    total = 0.
                    for number, deck in enumerate(decks, 1):
                        net, traces, _ = play_hand(players, deck, number, button=(number-1)%2)
                        total += net[seat]/10
                        for trace in traces:
                            if trace.get("match") == hero.match_id:
                                decisions += 1
                                fallbacks += bool(trace.get("fallback"))
                                exceptions += trace.get("attribution", {}).get("fallback_class") == "engine_exception"
                                interventions += bool(trace.get("river_defense", {}).get("applied"))
                    scores.append(100*total/hands)
                pair["arms"][mode] = {"bb_per_100": sum(scores)/2, "seat_scores": scores,
                    "interventions": interventions, "fallbacks": fallbacks, "decisions": decisions}
                pair["arms"][mode].update(exceptions=exceptions, legal_remaps=fallbacks-exceptions)
            pair["candidate_minus_control"] = pair["arms"]["candidate"]["bb_per_100"]-pair["arms"]["control"]["bb_per_100"]
            _write_new(output/f"pair-{index}.json", {"protocol": fingerprint, "payload": pair, "sha256": _sha(pair)})
            pair_rows.append(pair)
        if _files(ROOT, "submission") != manifest["runtime_files"] or _files(ROOT, "lab") != manifest["lab_files"]:
            raise RuntimeError("candidate source drift during evaluation")
        if _files(frozen_path, "submission") != manifest["opponent_files"]:
            raise RuntimeError("frozen opponent source drift during evaluation")
        result = {"protocol": fingerprint, "pairs": pair_rows, "oracle_probes": probes,
            "total_match_hands": pairs*hands*2*len(configs), "elapsed_seconds": perf_counter()-start,
            "strength_validated": False, "limitations": "Small exploratory paired fixed-stack evaluation; wall-clock work limits may vary. Oracle worlds are controlled examples, not inferred opponent distributions."}
        _write_new(output/"results.json", {"payload": result, "sha256": _sha(result)})
        return result
    except BaseException as exc:
        _write_new(output/"failure.json", {"type": type(exc).__name__, "message": str(exc), "protocol": fingerprint})
        raise


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", required=True)
    parser.add_argument("--frozen", required=True)
    parser.add_argument("--pairs", type=int, default=4)
    parser.add_argument("--hands", type=int, default=20)
    args = parser.parse_args()
    result = run(args.output, args.frozen, args.pairs, args.hands)
    print(json.dumps({"hands": result["total_match_hands"], "strength_validated": False,
                      "paired_deltas": [p["candidate_minus_control"] for p in result["pairs"]]}))
