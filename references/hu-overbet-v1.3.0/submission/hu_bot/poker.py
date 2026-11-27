"""Original pure-Python 5–7 card evaluator. Larger tuple always wins."""
from collections import Counter
from dataclasses import dataclass

RANKS = "23456789TJQKA"
DECK = tuple(r+s for r in RANKS for s in "cdhs")


def straight_high(ranks):
    ranks = set(ranks)
    if 14 in ranks:
        ranks.add(1)
    for high in range(14, 4, -1):
        if all(r in ranks for r in range(high-4, high+1)):
            return high
    return 0


def evaluate(cards):
    if not 5 <= len(cards) <= 7:
        raise ValueError("Evaluator expects 5–7 cards")
    ranks = [RANKS.index(c[0])+2 for c in cards]
    counts = Counter(ranks)
    suits = {}
    for c, r in zip(cards, ranks):
        suits.setdefault(c[1], []).append(r)
    flush = next((sorted(rs, reverse=True) for rs in suits.values() if len(rs) >= 5), [])
    if flush and (high := straight_high(flush)):
        return (8, high)
    groups = sorted(((n, r) for r, n in counts.items()), reverse=True)
    if groups[0][0] == 4:
        q = groups[0][1]
        return (7, q, max(r for r in ranks if r != q))
    trips = sorted((r for r, n in counts.items() if n >= 3), reverse=True)
    pairs = sorted((r for r, n in counts.items() if n >= 2), reverse=True)
    if trips and any(p != trips[0] for p in pairs):
        return (6, trips[0], max(p for p in pairs if p != trips[0]))
    if flush:
        return (5, *flush[:5])
    if high := straight_high(ranks):
        return (4, high)
    if trips:
        return (3, trips[0], *sorted((r for r in counts if r != trips[0]), reverse=True)[:2])
    if len(pairs) >= 2:
        return (2, *pairs[:2], max(r for r in ranks if r not in pairs[:2]))
    if pairs:
        return (1, pairs[0], *sorted((r for r in counts if r != pairs[0]), reverse=True)[:3])
    return (0, *sorted(counts, reverse=True)[:5])


def hand_class(cards):
    a, b = sorted(cards, key=lambda c: RANKS.index(c[0]), reverse=True)
    return a[0]+b[0]+("" if a[0] == b[0] else "s" if a[1] == b[1] else "o")


def preflop_score(cards):
    """Transparent heuristic ordering, NOT a solved equity or GTO chart."""
    a, b = sorted((RANKS.index(c[0])+2 for c in cards), reverse=True)
    if a == b:
        return min(1.0, .47 + a*.037)
    suited = cards[0][1] == cards[1][1]
    score = .11 + .026*a + .017*b + .065*suited
    score += .05 if a == 14 else 0
    score += .04 if a-b == 1 else .015 if a-b == 2 else 0
    score -= .015*max(0, a-b-3) if a != 14 else 0
    return min(.92, max(.12, score))


@dataclass(frozen=True)
class Features:
    made: str
    category: int
    rank: tuple
    draws: tuple[str, ...]
    texture: tuple[str, ...]
    wet: bool
    board_only: bool
    strength: float
    blocker: bool


def features(hole, board):
    if not board:
        return Features("PREFLOP", -1, (), (), (), False, False,
                        preflop_score(hole), any(c[0] == "A" for c in hole))
    rank = evaluate(tuple(hole)+tuple(board))
    br = [RANKS.index(c[0])+2 for c in board]
    hr = [RANKS.index(c[0])+2 for c in hole]
    bc = Counter(br)
    suits = Counter(c[1] for c in board)
    texture = ["HIGH" if max(br) >= 11 else "LOW"]
    if max(bc.values()) > 1:
        texture.append("PAIRED")
    if max(suits.values()) >= 3:
        texture.append("MONOTONE")
    elif max(suits.values()) == 2:
        texture.append("TWO_TONE")
    extended = set(br) | ({1} if 14 in br else set())
    connected = max(sum(r in extended for r in range(k, k+5)) for k in range(1, 11))
    if connected >= 3:
        texture.append("VERY_CONNECTED" if connected >= 4 else "CONNECTED")
    wet = connected >= 3 or max(suits.values()) >= 3
    if not wet:
        texture.append("DRY")
    board_only = len(board) == 5 and evaluate(board) == rank
    category = rank[0]
    # Absolute category is not relative hand strength: board trips/two-pair are weak.
    personal_pair = any(r in bc for r in hr) or hr[0] == hr[1]
    if board_only:
        made, strength = "WEAK_SHOWDOWN", .24
    elif category >= 4:
        made, strength = "VERY_STRONG", .92 if category >= 6 else .84
    elif category == 3 and any(r == rank[1] for r in hr):
        made, strength = "VERY_STRONG", .87
    elif category == 2 and (hr[0] != hr[1] and all(r in bc for r in hr)
                            and len(set(hr)) == 2):
        made, strength = "STRONG", .79
    elif personal_pair:
        pair_rank = max((r for r in hr if r in bc), default=hr[0] if hr[0] == hr[1] else 0)
        if pair_rank >= max(br):
            kicker = max((r for r in hr if r != pair_rank), default=14)
            made, strength = ("STRONG", .72) if kicker >= 11 else ("MEDIUM", .60)
        else:
            made, strength = "MEDIUM", .47
    else:
        made, strength = ("WEAK_SHOWDOWN", .30) if max(hr) == 14 or category else ("AIR", .16)
    draws = []
    if len(board) < 5:
        total_suits = Counter(c[1] for c in (*hole, *board))
        if any(n == 4 and any(c[1] == suit for c in hole) for suit, n in total_suits.items()):
            draws.append("FLUSH_DRAW")
        allr = set(br+hr) | ({1} if 14 in br+hr else set())
        missing = set()
        if category < 4:
            for k in range(1, 11):
                window = set(range(k, k+5))
                if len(window-allr) == 1 and any(r in window and r not in br for r in hr):
                    missing.update(window-allr)
            if missing:
                draws.append("OESD" if len(missing) >= 2 else "GUTSHOT")
        if len(draws) >= 2:
            draws.append("COMBO_DRAW")
        if min(hr) > max(br):
            draws.append("OVERCARDS")
        if len(board) == 3 and any(n == 3 and any(c[1] == suit for c in hole)
                                   for suit, n in total_suits.items()):
            draws.append("BACKDOOR")
    blocker = any(c[0] == "A" and suits.get(c[1], 0) >= 3 for c in hole)
    return Features(made, category, rank, tuple(draws), tuple(texture), wet,
                    board_only, strength, blocker)
