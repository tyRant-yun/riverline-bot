"""Compile original, explicitly authored ranges to a reviewable 169-class policy grid.

These are heuristic training wheels, not equilibrium charts or solver exports.
Runtime never calls this generator or a hand-strength scalar to choose preflop actions.
"""
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RANKS = "23456789TJQKA"
CLASSES = [a+b+("" if a == b else suffix) for i, a in enumerate(RANKS)
           for b in RANKS[:i+1] for suffix in (("",) if a == b else ("s", "o"))]
ACTIONS = ["FOLD", "CHECK", "CALL", "RAISE_SMALL", "RAISE_MEDIUM", "ALL_IN"]
DEPTHS = [5, 8, 12, 20, 35, 60, 100]
PRICES = [2, 3, 5]
CONTEXTS = ["BTN_OPEN", "BB_LIMP", "BB_OPEN", "BTN_ISOLATION", "VS_3BET", "VS_4BET", "VS_JAM"]


def expand(notation):
    result = set()
    for token in notation.split():
        if not token.endswith("+"):
            if token not in CLASSES:
                raise ValueError(token)
            result.add(token)
            continue
        base = token[:-1]
        if len(base) == 2 and base[0] == base[1]:
            result.update(r+r for r in RANKS[RANKS.index(base[0]):])
        else:
            high, low, suffix = base
            result.update(high+r+suffix for r in RANKS[RANKS.index(low):RANKS.index(high)])
    return result


RANGES = {
    "premium": "QQ+ AKs AKo",
    "value": "99+ ATs+ AJo+ KQs",
    "broad": "55+ A2s+ A8o+ K9s+ KTo+ Q9s+ QTo+ J9s+ JTo T9s 98s 87s 76s",
    "playable": "22+ A2s+ A2o+ K2s+ K7o+ Q5s+ Q8o+ J6s+ J8o+ T6s+ T8o+ 96s 97s 98s 98o 86s 87s 76s 75s 65s 64s 54s",
    "min_defend": "22+ A2s+ A2o+ K2s+ K2o+ Q2s+ Q5o+ J2s+ J7o+ T3s+ T7o+ 93s 94s 95s 96s 97s 98s 97o 98o 84s 85s 86s 87s 86o 87o 74s 75s 76s 76o 63s 64s 65s 53s 54s 43s",
    "vs3": "55+ A8s+ ATo+ KTs+ KQo QTs+ JTs T9s 98s",
    "bluff3": "A2s A3s A4s A5s K5s K6s Q8s J8s T8s 97s 86s 75s 65s",
    "jam5": "22+ A2s+ A2o+ K2s+ K2o+ Q2s+ Q5o+ J4s+ J7o+ T5s+ T8o+ 95s 96s 97s 98s 98o 85s 86s 87s 74s 75s 76s 64s 65s 54s",
    "jam8": "22+ A2s+ A2o+ K5s+ K9o+ Q8s+ QTo+ J8s+ JTo T8s+ 98s 87s 76s",
    "call5": "22+ A2s+ A2o+ K2s+ K5o+ Q5s+ Q8o+ J7s+ J9o+ T8s+ T9o 98s 87s",
    "call8": "22+ A2s+ A5o+ K7s+ KTo+ Q9s+ QTo+ J9s+ JTo T9s",
    "call12": "33+ A5s+ A8o+ KTs+ KQo QJs",
    "call20": "55+ ATs+ AJo+ KQs",
    "call35": "88+ AQs+ AKo",
    "call60": "JJ+ AKs AKo",
    "call100": "KK+",
}
GROUPS = {k: expand(v) for k, v in RANGES.items()}


def authored_policy(context, depth, price, hand):
    premium = hand in GROUPS["premium"]
    value = hand in GROUPS["value"]
    broad = hand in GROUPS["broad"]
    playable = hand in GROUPS["playable"]
    pair = len(hand) == 2
    suited = hand.endswith("s")
    if context == "VS_JAM":
        group = GROUPS[f"call{depth}"]
        if hand in group:
            return {"CALL": 1}
        if depth == 100 and hand in {"QQ", "AKs", "AKo"}:
            return {"CALL": .25, "FOLD": .75}
        return {"FOLD": 1}
    if context == "BTN_OPEN":
        if depth <= 8:
            return {"ALL_IN": 1} if hand in GROUPS[f"jam{depth}"] else {"FOLD": 1}
        if premium:
            return {"RAISE_SMALL": 1}
        if depth == 12 and (pair or hand in expand("A2s+ A8o+ KQs")):
            return {"ALL_IN": .65, "RAISE_SMALL": .20, "CALL": .15}
        if broad:
            return {"RAISE_SMALL": .85, "CALL": .15}
        if playable or suited:
            return {"RAISE_SMALL": .65, "CALL": .25, "FOLD": .10}
        return {"RAISE_SMALL": .20, "CALL": .25, "FOLD": .55}
    if context == "BB_LIMP":
        if depth <= 8:
            return {"ALL_IN": .80, "CHECK": .20} if hand in GROUPS["jam8"] else {"CHECK": 1}
        if premium or value:
            return {"ALL_IN": .70, "RAISE_MEDIUM": .30} if depth <= 12 else {"RAISE_MEDIUM": 1}
        if broad:
            return {"RAISE_SMALL": .40, "CHECK": .60}
        return {"RAISE_SMALL": .12, "CHECK": .88} if suited else {"CHECK": 1}
    if context in {"BB_OPEN", "BTN_ISOLATION"}:
        if depth <= 8:
            return {"ALL_IN": 1} if hand in GROUPS["call8"] else {"FOLD": 1}
        if premium:
            return {"ALL_IN" if depth <= 20 else "RAISE_MEDIUM": 1}
        if depth <= 20 and hand in GROUPS["call20"]:
            return {"ALL_IN": .75, "CALL": .25}
        if value:
            return {"RAISE_MEDIUM": .60, "CALL": .40}
        if hand in GROUPS["bluff3"] and depth >= 35 and price <= 3:
            return {"RAISE_MEDIUM": .20, "CALL": .65, "FOLD": .15}
        defense = GROUPS["min_defend"] if price == 2 else GROUPS["playable"] if price == 3 else GROUPS["broad"]
        if hand in defense:
            return {"CALL": .92 if price == 2 else .85, "FOLD": .08 if price == 2 else .15}
        return {"CALL": .12 if price == 2 else .03, "FOLD": .88 if price == 2 else .97}
    if context == "VS_3BET":
        if premium:
            return {"ALL_IN": .9, "CALL": .1} if depth <= 35 else {"RAISE_SMALL": .65, "CALL": .35}
        if depth <= 20:
            return {"ALL_IN": .80, "CALL": .20} if hand in GROUPS["call20"] else {"FOLD": 1}
        if hand in GROUPS["vs3"]:
            return {"CALL": .90 if price <= 3 else .55, "FOLD": .10 if price <= 3 else .45}
        if hand in expand("A2s A3s A4s A5s") and price <= 3:
            return {"RAISE_SMALL": .12, "CALL": .20, "FOLD": .68}
        return {"FOLD": 1}
    if context == "VS_4BET":
        if hand in {"AA", "KK"}:
            return {"ALL_IN": 1}
        if premium:
            return {"ALL_IN": .8, "CALL": .2} if depth <= 35 else {"CALL": .7, "FOLD": .3}
        if hand in expand("TT JJ AQs") and depth <= 20:
            return {"ALL_IN": .7, "FOLD": .3}
        return {"FOLD": 1}
    raise ValueError(context)


def generate():
    tables = {}
    for context in CONTEXTS:
        for depth in DEPTHS:
            for price in PRICES:
                tables[f"{context}:{depth}:{price}"] = [
                    [authored_policy(context, depth, price, h).get(a, 0) for a in ACTIONS]
                    for h in CLASSES]
    return {"schema_version": 1, "version": "1.1.0", "source": "original-authored-heuristic",
            "disclaimer": "Not GTO, not solver-derived. Review explicit ranges in tools/generate_preflop.py.",
            "depth_anchors_bb": DEPTHS, "price_anchors": PRICES,
            "price_semantics": "open raise-to / BB; reraise-to / previous opponent bet",
            "actions": ACTIONS, "classes": CLASSES, "authored_ranges": RANGES, "tables": tables}


if __name__ == "__main__":
    data = (json.dumps(generate(), separators=(",", ":"), sort_keys=True)+"\n").encode()
    target = ROOT/"submission/hu_bot/preflop_tables.json"
    target.write_bytes(data)
    print(json.dumps({"path": str(target), "bytes": len(data),
                      "rows": len(CONTEXTS)*len(DEPTHS)*len(PRICES)*169,
                      "sha256": hashlib.sha256(data).hexdigest()}))
