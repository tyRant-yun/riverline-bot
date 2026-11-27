"""Stable contracts: normalized state, node, policy and reproducible RNG."""
from dataclasses import dataclass
import hashlib
import json
import random

AGGRESSIVE = {"raise", "all_in"}
STREETS = {"preflop": 0, "flop": 3, "turn": 4, "river": 5}


@dataclass(frozen=True)
class Node:
    id: str
    pot_type: str
    raises: int
    street_raises: int
    last_aggressor: int | None


@dataclass(frozen=True)
class NormalizedState:
    hand_number: int
    street: str
    hero_cards: tuple[str, ...]
    board: tuple[str, ...]
    hero_seat: int
    dealer_seat: int
    position: str
    in_position: bool
    pot: int
    to_call: int
    hero_stack: int
    villain_stack: int
    effective_stack: int
    effective_stack_bb: float
    stack_bucket: str
    spr: float
    big_blind: int
    hero_bet: int
    villain_bet: int
    min_raise: int
    max_raise: int
    valid_actions: tuple[str, ...]
    action_history: tuple[dict, ...]
    node: Node
    round_id: str
    request_id: str
    num_players: int


def street_bets(history, street):
    """Reconstruct totals; calls match the table bet (history examples are ambiguous).

    Wire turn_result calls are increments, while older ActionEntry examples use
    totals. Matching the outstanding bet avoids interpreting either as a raise.
    Short all-in calls cannot lead to another HU decision on the same street.
    Antes contribute to the pot, never to the amount needed to call.
    """
    bets = {}
    for e in history:
        if e.get("phase", "preflop") != street:
            continue
        seat, action, amount = int(e["seat"]), e["action"], int(e.get("amount", 0))
        if action in {"post_small_blind", "post_big_blind", "raise"}:
            bets[seat] = max(bets.get(seat, 0), amount)
        elif action == "call":
            bets[seat] = max(bets.values(), default=bets.get(seat, 0))
        elif action == "all_in":
            bets[seat] = max(bets.get(seat, 0), amount)
    return bets


def stack_bucket(bb):
    for threshold, name in ((80, "DEEP"), (50, "DEEP_NORMAL"), (30, "NORMAL"),
                            (20, "MID"), (12, "SHALLOW"), (8, "VERY_SHALLOW")):
        if bb >= threshold:
            return name
    return "PUSH_FOLD"


def classify_node(street, hero, dealer, history, to_call):
    pf = [e for e in history if e.get("phase", "preflop") == "preflop"
          and not e["action"].startswith("post_")]
    raises = [e for e in pf if e["action"] in AGGRESSIVE]
    current = [e for e in history if e.get("phase", "preflop") == street
               and e["action"] in AGGRESSIVE]
    n = len(raises)
    pot_type = "LIMP" if n == 0 else "SRP" if n == 1 else "3BET" if n == 2 else "4BET"
    pos = "BTN" if hero == dealer else "BB"
    if street == "preflop":
        if not pf:
            suffix = "FIRST_IN" if pos == "BTN" else "OPTION"
        elif n == 0:
            suffix = "VS_LIMP" if pos == "BB" else "FIRST_IN"
        elif n == 1:
            suffix = "VS_LIMP_RAISE" if pos == "BTN" else "VS_OPEN"
        else:
            suffix = "VS_3BET" if n == 2 else "VS_4BET"
        node_id = f"PREFLOP_{pos}_{suffix}"
    else:
        xr = any(e["action"] == "check" and any(
            x["seat"] == e["seat"] and x["action"] in AGGRESSIVE
            for x in history[i+1:] if x.get("phase") == street)
            for i, e in enumerate(history) if e.get("phase") == street)
        node_id = f"{pot_type}_{street.upper()}_{'IP' if pos == 'BTN' else 'OOP'}"
        if xr:
            node_id += "_XR"
        elif to_call:
            node_id += "_VS_BET"
    return Node(node_id, pot_type, n, len(current),
                int(raises[-1]["seat"]) if raises else None)


def normalize(raw, big_blind=10):
    history = tuple(dict(e) for e in raw.action_history)
    posted = [int(e["amount"]) for e in history if e["action"] == "post_big_blind"]
    # A short BB post isn't necessarily the nominal blind; round_start/config is preferred
    # when it is larger. Normal complete posts track blind increases without hardcoding.
    bb = max(1, big_blind, max(posted, default=0))
    bets = street_bets(history, raw.phase)
    hero_bet = bets.get(raw.your_seat, 0)
    villain_bet = max((v for k, v in bets.items() if k != raw.your_seat), default=0)
    # Authoritative to_call also repairs truncated histories on reconnect.
    villain_bet = max(villain_bet, hero_bet + raw.to_call)
    hero_bet = max(hero_bet, villain_bet - raw.to_call)
    villain = max(raw.opponent_stacks, default=0)
    effective = min(raw.your_stack, villain + raw.to_call)
    # Preflop charts use street-start effective depth, including posted bets.
    depth = min(raw.your_stack + hero_bet, villain + villain_bet) / bb
    node = classify_node(raw.phase, raw.your_seat, raw.dealer_seat, history, raw.to_call)
    return NormalizedState(raw.hand_number, raw.phase, tuple(map(str, raw.hole_cards)),
        tuple(map(str, raw.board)), raw.your_seat, raw.dealer_seat,
        "BTN" if raw.your_seat == raw.dealer_seat else "BB",
        raw.your_seat == raw.dealer_seat, raw.pot, min(raw.to_call, raw.your_stack),
        raw.your_stack, villain, effective, depth, stack_bucket(depth),
        effective / max(1, raw.pot), bb, hero_bet, villain_bet,
        raw.min_raise, raw.max_raise, tuple(raw.valid_actions), history, node,
        raw.round_id, raw.request_id, len(raw.opponent_stacks) + 1)


@dataclass(frozen=True)
class Policy:
    weights: dict[str, float]
    reason: str = ""

    def normalized(self):
        clean = {k: max(0.0, float(v)) for k, v in self.weights.items() if v > 0}
        total = sum(clean.values())
        if not total:
            raise ValueError("Empty policy")
        return Policy({k: v / total for k, v in clean.items()}, self.reason)

    def mix(self, other, weight):
        a, b = self.normalized(), other.normalized()
        return Policy({k: (1-weight)*a.weights.get(k, 0)+weight*b.weights.get(k, 0)
                       for k in sorted(a.weights.keys() | b.weights.keys())},
                      self.reason).normalized()


class RNGManager:
    def __init__(self, seed, match_id="offline"):
        self.seed, self.match_id = seed, match_id

    def for_state(self, state, stream="selection"):
        # Identity is state-derived, so duplicate requests produce the same policy sample.
        payload = [self.seed, self.match_id, state.hand_number, state.round_id,
                   state.street, state.hero_cards, state.board, state.hero_seat,
                   state.pot, state.to_call, state.action_history, stream]
        digest = hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).digest()
        seed = int.from_bytes(digest[:8], "big")
        return seed, random.Random(seed)
