"""Complete playable v2 baseline policy.

This is deliberately a conservative, model-limited baseline. It uses only the
hero cards, public board, public action history, current prices and legal SDK
actions. It is not a solver policy and does not claim calibrated EV.
"""

from dataclasses import dataclass
import hashlib

from chipzen import Action

from .poker import features
from .v2_contracts import V2Snapshot


@dataclass(frozen=True, slots=True)
class PolicyDecision:
    label: str
    reason: str
    strength: float
    model_status: str = "model_limited"
    target_raise_to: int | None = None


def _paired(snapshot):
    ranks = [card[0] for card in snapshot.board]
    return len(ranks) != len(set(ranks))


def _draw(features_value):
    return bool(set(features_value.draws) & {"FLUSH_DRAW", "OESD", "GUTSHOT", "COMBO_DRAW"})


RANK_VALUE = {rank: index for index, rank in enumerate("23456789TJQKA", start=2)}

GRAY_PLAYABLE_LIMP_CLASSES = frozenset({
    "J4o", "Q3o",
})
GRAY_ISO_CALL_CLASSES = frozenset({"J4o", "Q3o"})
LIMP_VALUE_CLASSES = frozenset({"AA", "KK", "AKs", "AQs"})
LIMP_BLOCKER_CLASSES = frozenset({"A5s"})
LIMP_PAIR_CLASSES = frozenset({"55", "66", "77", "88", "99"})
LIMP_STRUCTURE_CLASSES = frozenset({
    "76s", "87s", "98s", "T9s", "JTs", "QJs", "A4s",
})
LIMP_STRUCTURE_6BB_CLASSES = frozenset({"98s", "T9s", "JTs", "QJs"})


def _class_profile(cards):
    first, second = cards
    high, low = sorted((RANK_VALUE[first[0]], RANK_VALUE[second[0]]), reverse=True)
    suited = first[1] == second[1]
    pair = high == low
    gap = high - low
    strong = (pair and high >= 7) or (high == 14 and (suited or low >= 10))
    strong = strong or (suited and high >= 13 and low >= 9)
    strong = strong or (suited and high >= 12 and low >= 10)
    strong = strong or (not suited and high >= 13 and low >= 11)
    # A low offsuit gap is not a reliable realization class at a 7--10BB
    # 3bet. Keep the continue bucket explicit: pairs, suitable suited hands,
    # and only higher one-gap offsuit connectors.
    speculative = (pair
                   or (suited and (high >= 8 or (high >= 6 and gap <= 2)))
                   or (not suited and
                       ((high >= 11 and gap <= 2) or (high == 10 and gap <= 1))))
    premium = (pair and high >= 11) or (high == 14 and low >= 13)
    class_label = f"{chr(48 + high) if high < 10 else {10: 'T', 11: 'J', 12: 'Q', 13: 'K', 14: 'A'}[high]}{chr(48 + low) if low < 10 else {10: 'T', 11: 'J', 12: 'Q', 13: 'K', 14: 'A'}[low]}{'s' if suited and not pair else 'o' if not suited and not pair else ''}"
    pdf_gray = class_label in {
        "J4o", "T4o", "94o", "84o", "Q3o", "J3o", "T3o", "93o",
        "83o", "73o", "63o", "Q2o", "J2o", "T2o", "92o", "82o", "72o",
        "62o", "52o", "42o", "32o",
    }
    extreme_offsuit = class_label in {"72o", "52o", "42o", "32o"}
    openable = not pdf_gray
    three_bet_structure = class_label in {
        "A2s", "A3s", "A4s", "A5s", "55", "66", "76s", "87s", "98s", "T9s",
    }
    return {"high": high, "low": low, "suited": suited, "pair": pair,
            "strong": strong, "speculative": speculative, "premium": premium,
            "openable": openable, "pdf_gray": pdf_gray,
            "extreme_offsuit": extreme_offsuit,
            "defendable_vs_open_proxy": not pdf_gray,
            "three_bet_structure": three_bet_structure,
            "class_label": class_label, "gap": gap}


def _simple_preflop_domain(snapshot):
    return (snapshot.ledger.status == "consistent"
            and snapshot.ante_by_seat == (0, 0)
            and 80 <= snapshot.effective_stack_bb <= 120
            and not snapshot.preflop_off_grid)


def _continuous_3bet_price_domain(snapshot):
    """Allow the bounded 6–11BB 3bet corridor, including off-grid values."""
    return (snapshot.ledger.status == "consistent"
            and snapshot.ante_by_seat == (0, 0)
            and 80 <= snapshot.effective_stack_bb <= 120
            and snapshot.preflop_context == "VS_3BET"
            and 6.0 <= snapshot.node_key.three_bet_to / max(1, snapshot.big_blind) <= 11.0)


def _candidate_target(snapshot, index=0):
    candidates = preflop_price_candidates(snapshot)
    targets = candidates["raise_to"]
    return targets[index] if len(targets) > index else None


def _limp_protection_kind(snapshot):
    class_label = _class_profile(snapshot.hero_cards)["class_label"]
    if class_label not in (LIMP_VALUE_CLASSES | LIMP_BLOCKER_CLASSES
                           | LIMP_PAIR_CLASSES | LIMP_STRUCTURE_CLASSES):
        return ""
    token = "|".join(sorted(snapshot.hero_cards)).encode("ascii")
    slot = int.from_bytes(hashlib.sha256(token).digest()[:2], "big")
    if class_label in LIMP_VALUE_CLASSES and slot % 4 == 0:
        return "value"
    if class_label in LIMP_BLOCKER_CLASSES and slot % 4 == 0:
        return "blocker"
    if class_label in LIMP_PAIR_CLASSES and slot % 3 == 0:
        return "pair"
    if class_label in LIMP_STRUCTURE_CLASSES and slot % 2 == 0:
        return "structure"
    return ""


def _btn_3bet_raise_profile(profile):
    """Small PDF-direction pressure mix, not a calibrated 4bet range."""
    value_core = ((profile["pair"] and profile["high"] >= 10)
                  or (profile["high"] == 14 and profile["low"] == 13))
    blocker_mix = (profile["suited"] and profile["high"] == 14
                   and profile["low"] in {4, 5})
    return value_core, blocker_mix


def _simple_btn_first_in(snapshot, score):
    profile = _class_profile(snapshot.hero_cards)
    if _limp_protection_kind(snapshot) and "call" in snapshot.valid_actions:
        return PolicyDecision("CALL", "pdf_pressure_limp_protection_mix", score)
    if profile["openable"] and "raise" in snapshot.valid_actions:
        # Yellow cells use one real raise-to size; strength is expressed by
        # which hands enter, not by a size tell.
        target = round(2.5 * snapshot.big_blind)
        return PolicyDecision("OPEN_SMALL", "pdf_pressure_uniform_2p5_open", score,
                              target_raise_to=max(snapshot.min_raise,
                                                  min(snapshot.max_raise, target)))
    if (profile["pdf_gray"]
            and profile["class_label"] in GRAY_PLAYABLE_LIMP_CLASSES
            and "call" in snapshot.valid_actions):
        return PolicyDecision("CALL", "pdf_pressure_speculative_limp_realization", score)
    if "fold" in snapshot.valid_actions:
        return PolicyDecision("FOLD", "pdf_pressure_offsuit_junk_control", score)
    return _legacy_preflop(snapshot, score)


def _simple_bb_vs_open(snapshot, score):
    profile = _class_profile(snapshot.hero_cards)
    if ((profile["strong"] or profile["three_bet_structure"])
            and "raise" in snapshot.valid_actions):
        target = max(snapshot.min_raise, min(snapshot.max_raise,
                                            round(4 * snapshot.node_key.open_to)))
        reason = ("pdf_pressure_linear_4x_3bet"
                  if profile["strong"] else "pdf_pressure_structure_3bet_mix")
        return PolicyDecision("RAISE_MEDIUM", reason, score,
                              target_raise_to=target)
    odds = snapshot.to_call / max(1, snapshot.pot + snapshot.to_call)
    price_bb = snapshot.node_key.open_to / max(1, snapshot.big_blind)
    if (profile["defendable_vs_open_proxy"] and price_bb <= 2.6
            and odds <= .40 and "call" in snapshot.valid_actions):
        return PolicyDecision("CALL", "pdf_pressure_bb_high_card_defense", score)
    if "fold" in snapshot.valid_actions:
        return PolicyDecision("FOLD", "pdf_pressure_bb_junk_control", score)
    return _legacy_preflop(snapshot, score)


def _simple_vs_limp_raise(snapshot, score):
    """BTN response to a BB iso after BTN completed; never uses BB-vs-open."""
    profile = _class_profile(snapshot.hero_cards)
    protection = _limp_protection_kind(snapshot)
    iso_to = snapshot.node_key.open_to
    iso_bb = iso_to / max(1, snapshot.big_blind)
    if ((protection in {"value", "blocker"} or profile["premium"])
            and "raise" in snapshot.valid_actions):
        target = max(snapshot.min_raise, min(snapshot.max_raise,
                                             round(3 * iso_to)))
        reason = ("protected_limp_reraise_blocker"
                  if protection == "blocker" else "protected_limp_reraise_value")
        return PolicyDecision("RAISE_MEDIUM", reason, score,
                              target_raise_to=target)
    if protection == "structure" and "call" in snapshot.valid_actions:
        if iso_bb <= 5.0 or (
                profile["class_label"] in LIMP_STRUCTURE_6BB_CLASSES and iso_bb <= 6.0
        ):
            return PolicyDecision("CALL", "protected_limp_iso_structure_call", score)
    if protection == "pair" and "call" in snapshot.valid_actions:
        if iso_bb <= 5.0 or (profile["high"] >= 8 and iso_bb <= 6.0):
            return PolicyDecision("CALL", "protected_limp_iso_pair_call", score)
    if (profile["pdf_gray"] and profile["class_label"] in GRAY_ISO_CALL_CLASSES
            and iso_bb <= 3.0 and "call" in snapshot.valid_actions):
        return PolicyDecision("CALL", "gray_limp_price_defense", score)
    odds = snapshot.to_call / max(1, snapshot.pot + snapshot.to_call)
    if profile["defendable_vs_open_proxy"] and odds <= .36 and "call" in snapshot.valid_actions:
        return PolicyDecision("CALL", "protected_limp_iso_price_defense", score)
    if "fold" in snapshot.valid_actions:
        return PolicyDecision("FOLD", "protected_limp_iso_control", score)
    return _legacy_preflop(snapshot, score)


def _simple_btn_vs_3bet(snapshot, score):
    profile = _class_profile(snapshot.hero_cards)
    value_core, blocker_mix = _btn_3bet_raise_profile(profile)
    if (value_core or blocker_mix) and "raise" in snapshot.valid_actions:
        target = _candidate_target(snapshot)
        if target is not None:
            reason = ("pdf_pressure_value_core_2p2x_4bet"
                      if value_core else "pdf_pressure_blocker_2p2x_4bet")
            return PolicyDecision("RAISE_LARGE", reason, score,
                                  target_raise_to=target)
    odds = snapshot.to_call / max(1, snapshot.pot + snapshot.to_call)
    if profile["speculative"] and odds <= .40 and "call" in snapshot.valid_actions:
        return PolicyDecision("CALL", "pdf_pressure_3bet_price_call", score)
    if "fold" in snapshot.valid_actions:
        return PolicyDecision("FOLD", "pdf_pressure_vs_3bet_junk_control", score)
    return _legacy_preflop(snapshot, score)


def _safe_btn_vs_3bet_boundary(snapshot, score):
    """Keep out-of-corridor BTN-vs-3bet prices conservative and legal."""
    profile = _class_profile(snapshot.hero_cards)
    value_core, blocker_mix = _btn_3bet_raise_profile(profile)
    if (value_core or blocker_mix) and "raise" in snapshot.valid_actions:
        target = _candidate_target(snapshot)
        if target is not None:
            reason = ("pdf_pressure_boundary_value_core_2p2x_4bet"
                      if value_core else "pdf_pressure_boundary_blocker_2p2x_4bet")
            return PolicyDecision("RAISE_LARGE", reason, score,
                                  target_raise_to=target)
    if "fold" in snapshot.valid_actions:
        return PolicyDecision("FOLD", "pdf_pressure_boundary_3bet_control", score)
    return _legacy_preflop(snapshot, score)


def _simple_bb_vs_4bet(snapshot, score):
    profile = _class_profile(snapshot.hero_cards)
    if profile["premium"] and "raise" in snapshot.valid_actions:
        target = snapshot.max_raise if profile["pair"] and profile["high"] >= 13 else _candidate_target(snapshot)
        if target is not None:
            label = "ALL_IN" if target == snapshot.max_raise else "RAISE_LARGE"
            return PolicyDecision(label, "pdf_pressure_5bet_spr_control", score,
                                  target_raise_to=None if label == "ALL_IN" else target)
    odds = snapshot.to_call / max(1, snapshot.pot + snapshot.to_call)
    # Suitedness alone is not a 4bet-call license: 65s and similar hands are
    # folded at the 100BB 2.5 -> 10 -> 22 price until a conditioned Q model
    # proves the implied-odds and realization case.
    four_bet_call = (profile["pair"] and profile["high"] >= 8) or (
        profile["suited"] and profile["high"] >= 11 and profile["low"] >= 9)
    if four_bet_call and odds <= .36 and "call" in snapshot.valid_actions:
        return PolicyDecision("CALL", "pdf_pressure_4bet_price_call", score)
    if "fold" in snapshot.valid_actions:
        return PolicyDecision("FOLD", "pdf_pressure_vs_4bet_control", score)
    return _legacy_preflop(snapshot, score)


def _current_aggression(snapshot):
    return [event for event in snapshot.action_history
            if event.get("phase") == snapshot.street
            and event.get("action") in {"raise", "all_in"}]


def _legacy_preflop(snapshot, score):
    if snapshot.ledger.status != "consistent":
        if snapshot.to_call and "fold" in snapshot.valid_actions:
            return PolicyDecision("FOLD", "ledger_conflict_safe_baseline", score,
                                  "unscorable")
        return PolicyDecision("CHECK", "ledger_conflict_safe_baseline", score,
                              "unscorable")
    depth = snapshot.effective_stack_bb
    price = snapshot.villain_bet / max(1, snapshot.big_blind)
    raises = sum(event.get("action") in {"raise", "all_in"}
                 for event in snapshot.action_history
                 if event.get("phase", "preflop") == "preflop")
    # A blind completion is a real SDK call price but not an opponent wager.
    # Route it by node semantics before any price-defense logic.
    if not snapshot.facing_opponent_bet:
        if snapshot.position == "BTN" and snapshot.preflop_context == "FIRST_IN":
            threshold = .28 if depth >= 20 else .38 if depth >= 8 else .48
            if score >= threshold and "raise" in snapshot.valid_actions:
                return PolicyDecision("OPEN_SMALL", "button_first_in_baseline", score)
            if "call" in snapshot.valid_actions and score >= .20:
                return PolicyDecision("CALL", "button_first_in_complete", score)
            return PolicyDecision("FOLD", "button_first_in_fold", score)
        if snapshot.to_call == 0:
            if raises == 0 and score >= (.56 if depth >= 20 else .63) and "raise" in snapshot.valid_actions:
                return PolicyDecision("ISO_SMALL", "big_blind_limp_isolation", score)
            return PolicyDecision("CHECK", "big_blind_option_check", score)

    odds = snapshot.to_call / max(1, snapshot.pot + snapshot.to_call)
    if snapshot.facing_opponent_bet:
        if depth <= 8:
            if score >= .58 and "raise" in snapshot.valid_actions:
                return PolicyDecision("ALL_IN", "short_stack_value_jam", score)
            if score >= max(.47, odds + .04):
                return PolicyDecision("CALL", "short_stack_price_defense", score)
            return PolicyDecision("FOLD", "short_stack_conservative_fold", score)
        if score >= .78 and price <= 6 and "raise" in snapshot.valid_actions:
            return PolicyDecision("RAISE_LARGE", "premium_price_aware_3bet", score)
        # Keep the ordinary 3-bet route ahead of the price-call route. This
        # is only a baseline action family, not a solver/GTO assertion.
        if (score >= .64 and price <= 3.5 and "raise" in snapshot.valid_actions):
            reason = ("open_price_aware_3bet"
                      if snapshot.preflop_context == "VS_OPEN"
                      else "strong_price_aware_reraise")
            return PolicyDecision("RAISE_MEDIUM", reason, score)
        if score >= max(.30, odds + .06):
            return PolicyDecision("CALL", "price_aware_defense", score)
        return PolicyDecision("FOLD", "price_aware_fold", score)
    if snapshot.position == "BTN":
        return PolicyDecision("CHECK", "button_low_score_check", score)
    if raises >= 2 and score >= .78 and "raise" in snapshot.valid_actions:
        return PolicyDecision("RAISE_LARGE", "premium_reraise", score)
    return PolicyDecision("CHECK", "big_blind_option_check", score)


def _preflop_first_in(snapshot, score):
    """Explicit BTN-first-in node with a small, default-on 100BB slice."""
    if _simple_preflop_domain(snapshot):
        return _simple_btn_first_in(snapshot, score)
    return _legacy_preflop(snapshot, score)


def _preflop_vs_open(snapshot, score):
    """Explicit BB-vs-open node with price-keyed integer 3bet targets."""
    if _simple_preflop_domain(snapshot):
        return _simple_bb_vs_open(snapshot, score)
    return _legacy_preflop(snapshot, score)


def _preflop_vs_limp_raise(snapshot, score):
    """Explicit limp/iso node, separate from BB facing a voluntary open."""
    if _simple_preflop_domain(snapshot):
        return _simple_vs_limp_raise(snapshot, score)
    return _legacy_preflop(snapshot, score)


def _preflop_vs_3bet(snapshot, score):
    """Explicit BTN-vs-3bet node with a legal 2.2x 4bet target."""
    if _continuous_3bet_price_domain(snapshot):
        return _simple_btn_vs_3bet(snapshot, score)
    if snapshot.preflop_context == "VS_3BET":
        return _safe_btn_vs_3bet_boundary(snapshot, score)
    return _legacy_preflop(snapshot, score)


def _preflop_vs_4bet(snapshot, score):
    """Explicit BB-vs-4bet node with both non-all-in 5bet and jam paths."""
    if _simple_preflop_domain(snapshot):
        return _simple_bb_vs_4bet(snapshot, score)
    return _legacy_preflop(snapshot, score)


def _preflop(snapshot, score):
    branches = {
        "FIRST_IN": _preflop_first_in,
        "VS_OPEN": _preflop_vs_open,
        "VS_LIMP_RAISE": _preflop_vs_limp_raise,
        "VS_3BET": _preflop_vs_3bet,
        "VS_4BET": _preflop_vs_4bet,
    }
    return branches.get(snapshot.preflop_context, _legacy_preflop)(snapshot, score)


def _clamp_candidate(snapshot, target):
    if "raise" not in snapshot.valid_actions or snapshot.min_raise > snapshot.max_raise:
        return None
    return max(snapshot.min_raise, min(snapshot.max_raise, int(target)))


def preflop_price_candidates(snapshot):
    """Return auditable integer raise-to candidates without changing default play.

    The online baseline consumes none of these candidates yet.  Off-grid prices
    are retained as explicit keys and never silently mapped to a nearby table.
    """
    context = snapshot.preflop_context
    prices = dict(snapshot.preflop_prices)
    targets = []
    if context == "FIRST_IN":
        targets = [2 * snapshot.big_blind, round(2.25 * snapshot.big_blind),
                   round(2.5 * snapshot.big_blind)]
    elif context == "VS_OPEN" and prices.get("open_to", 0):
        targets = [round(3 * prices["open_to"]), round(4 * prices["open_to"])]
    elif context == "VS_LIMP_RAISE" and prices.get("open_to", 0):
        targets = [round(3 * prices["open_to"])]
    elif context == "VS_3BET" and prices.get("three_bet_to", 0):
        targets = [round(2.2 * prices["three_bet_to"])]
    elif context == "VS_4BET" and prices.get("four_bet_to", 0):
        targets = [round(2.2 * prices["four_bet_to"]), snapshot.max_raise]
    legal_targets = []
    for target in targets:
        candidate = _clamp_candidate(snapshot, target)
        if candidate is not None and candidate not in legal_targets:
            legal_targets.append(candidate)
    action_set = [name for name in ("fold", "call", "raise")
                  if name in snapshot.valid_actions]
    return {
        "context": context,
        "price_key": snapshot.preflop_price_key,
        "off_grid": snapshot.preflop_off_grid,
        "actions": action_set,
        "raise_to": legal_targets,
        "status": "candidate_only_default_strategy_unchanged",
    }


def choose(snapshot: V2Snapshot):
    hand_features = features(snapshot.hero_cards, snapshot.board)
    if snapshot.street == "preflop":
        return _preflop(snapshot, hand_features.strength), hand_features
    if snapshot.ledger.status != "consistent":
        label = "FOLD" if snapshot.to_call else "CHECK"
        return PolicyDecision(label, "ledger_conflict_safe_baseline",
                              hand_features.strength, "unscorable"), hand_features
    paired = _paired(snapshot)
    draw = _draw(hand_features)
    paired_flush_without_full_house = paired and hand_features.category == 5
    full_house_or_quads = hand_features.category >= 6
    strong = (hand_features.category >= 4 and not paired_flush_without_full_house) or (
        hand_features.category == 3 and not hand_features.board_only)
    two_pair = hand_features.category == 2 and not hand_features.board_only
    odds = snapshot.to_call / max(1, snapshot.pot + snapshot.to_call)
    aggression = _current_aggression(snapshot)
    if snapshot.to_call:
        if hand_features.board_only:
            return PolicyDecision("CALL" if odds <= .25 else "FOLD",
                                  "board_only_price_defense", hand_features.strength), hand_features
        if paired_flush_without_full_house:
            label = "CALL" if odds <= .18 else "FOLD"
            return PolicyDecision(label, "paired_board_non_nut_flush_caution",
                                  hand_features.strength), hand_features
        if strong and "raise" in snapshot.valid_actions:
            return PolicyDecision("RAISE_LARGE" if hand_features.category >= 5 else "RAISE_MEDIUM",
                                  "made_hand_value_response", hand_features.strength), hand_features
        if two_pair and paired:
            label = "CALL" if odds <= .28 else "FOLD"
            return PolicyDecision(label, "paired_board_two_pair_no_auto_raise",
                                  hand_features.strength), hand_features
        if draw and snapshot.street != "river" and odds <= .28:
            label = "RAISE_SMALL" if snapshot.position == "BTN" and not aggression else "CALL"
            return PolicyDecision(label, "priced_draw_response", hand_features.strength), hand_features
        if hand_features.strength >= max(.48, odds + .08) and odds <= .33:
            return PolicyDecision("CALL", "top_pair_or_showdown_defense", hand_features.strength), hand_features
        return PolicyDecision("FOLD", "weak_facing_bet", hand_features.strength), hand_features
    if hand_features.board_only:
        return PolicyDecision("CHECK", "board_only_showdown_control", hand_features.strength), hand_features
    if paired_flush_without_full_house and not full_house_or_quads:
        return PolicyDecision("CHECK", "paired_board_non_nut_flush_caution",
                              hand_features.strength), hand_features
    if strong:
        label = "BET_LARGE" if hand_features.category >= 5 else "BET_MEDIUM"
        return PolicyDecision(label, "made_hand_value_bet", hand_features.strength), hand_features
    if two_pair:
        return PolicyDecision("BET_SMALL" if not paired else "CHECK",
                              "two_pair_texture_control", hand_features.strength), hand_features
    if draw:
        return PolicyDecision("BET_MEDIUM" if snapshot.position == "BTN" else "CHECK",
                              "draw_pressure_or_control", hand_features.strength), hand_features
    if snapshot.position == "BTN" and (hand_features.blocker or not hand_features.wet):
        return PolicyDecision("BET_SMALL", "positioned_baseline_bluff", hand_features.strength), hand_features
    return PolicyDecision("CHECK", "out_of_position_air_control", hand_features.strength), hand_features


def _raise_to(snapshot, label):
    if label == "ALL_IN":
        return snapshot.max_raise
    fractions = {"OPEN_SMALL": .55, "ISO_SMALL": .75, "RAISE_SMALL": .50,
                 "RAISE_MEDIUM": .75, "RAISE_LARGE": 1.0, "BET_SMALL": .33,
                 "BET_MEDIUM": .66, "BET_LARGE": 1.0}
    base = snapshot.hero_bet + snapshot.to_call
    return base + round(fractions.get(label, .50) * max(snapshot.pot, snapshot.big_blind))


def legalize(snapshot, decision):
    label = decision.label
    if label == "FOLD" and "fold" in snapshot.valid_actions:
        return Action.fold(), None
    if label == "CHECK" and "check" in snapshot.valid_actions:
        return Action.check(), None
    if label == "CALL" and "call" in snapshot.valid_actions:
        return Action.call(), None
    if label in {"OPEN_SMALL", "ISO_SMALL", "RAISE_SMALL", "RAISE_MEDIUM",
                 "RAISE_LARGE", "BET_SMALL", "BET_MEDIUM", "BET_LARGE", "ALL_IN"}:
        if "raise" in snapshot.valid_actions and snapshot.min_raise <= snapshot.max_raise:
            target = (decision.target_raise_to if decision.target_raise_to is not None
                      else _raise_to(snapshot, label))
            return Action.raise_to(max(snapshot.min_raise,
                                       min(snapshot.max_raise, target))), None
        if label == "ALL_IN" and "all_in" in snapshot.valid_actions:
            return Action.all_in(), None
    for fallback in ("check", "call", "fold"):
        if fallback in snapshot.valid_actions:
            action = {"check": Action.check, "call": Action.call,
                      "fold": Action.fold}[fallback]()
            return action, f"{label}_unavailable_{fallback}"
    if "raise" in snapshot.valid_actions and snapshot.min_raise <= snapshot.max_raise:
        return Action.raise_to(snapshot.min_raise), f"{label}_fallback_min_raise"
    raise ValueError("No legal v2 action")

