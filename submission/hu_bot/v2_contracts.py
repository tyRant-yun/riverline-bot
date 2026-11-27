"""Small, auditable v2 facts layer for a complete heads-up decision."""

from dataclasses import asdict, dataclass
import hashlib
import json


@dataclass(frozen=True, slots=True)
class V2Ledger:
    displayed_pot: int
    matched_contestable_pot: int
    pending_unmatched_wager: int
    returned_uncalled: int
    hero_bet: int
    villain_bet: int
    status: str
    source: str


@dataclass(frozen=True, slots=True)
class NodeKey:
    street: str
    node_id: str
    position: str
    effective_stack_bb: float
    big_blind: int
    ante_by_seat: tuple[int, int]
    hand_start_stacks: tuple[int, int]
    pot: int
    to_call: int
    hero_bet: int
    villain_bet: int
    min_raise_to: int
    max_raise_to: int
    legal_actions: tuple[str, ...]
    action_line: tuple[str, ...]
    rule_source: str
    preflop_price_key: str = ""
    open_to: int = 0
    three_bet_to: int = 0
    four_bet_to: int = 0
    history_validity: str = "unknown"
    off_grid: bool = False

    @property
    def digest(self):
        payload = json.dumps(asdict(self), sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


@dataclass(frozen=True, slots=True)
class V2Snapshot:
    hand_number: int
    street: str
    hero_cards: tuple[str, str]
    board: tuple[str, ...]
    hero_seat: int
    dealer_seat: int
    position: str
    pot: int
    to_call: int
    hero_stack: int
    villain_stack: int
    effective_stack_bb: float
    big_blind: int
    ante_by_seat: tuple[int, int]
    hand_start_stacks: tuple[int, int]
    hero_bet: int
    villain_bet: int
    min_raise: int
    max_raise: int
    valid_actions: tuple[str, ...]
    action_history: tuple[dict, ...]
    preflop_context: str
    facing_opponent_bet: bool
    node_id: str
    ledger: V2Ledger
    node_key: NodeKey
    preflop_price_key: str = ""
    preflop_prices: tuple[tuple[str, int], ...] = ()
    preflop_off_grid: bool = False


def _seat_amounts(history, street):
    amounts = {0: 0, 1: 0}
    for event in history:
        if event.get("phase", "preflop") != street:
            continue
        seat = int(event.get("seat", 0))
        action = str(event.get("action", ""))
        amount = max(0, int(event.get("amount", 0)))
        if seat not in amounts:
            continue
        if action in {"post_small_blind", "post_big_blind", "post_ante", "raise", "all_in"}:
            amounts[seat] = max(amounts[seat], amount)
        elif action == "call":
            amounts[seat] = max(amounts.values())
    return amounts


def _antes(history):
    result = [0, 0]
    for event in history:
        if event.get("action") == "post_ante":
            seat = int(event.get("seat", 0))
            if seat in (0, 1):
                result[seat] += max(0, int(event.get("amount", 0)))
    return tuple(result)


def _preflop_context(position, hero_seat, history, to_call):
    active = [e for e in history
              if e.get("phase", "preflop") == "preflop"
              and not str(e.get("action", "")).startswith("post_")]
    raises = [e for e in active if e.get("action") in {"raise", "all_in"}]
    last = active[-1] if active else None
    if not raises:
        return ("FIRST_IN" if position == "BTN" and not active
                else "BB_LIMP_OPTION" if position == "BB" else "NO_RAISE_OPTION")
    last_raise = raises[-1]
    if int(last_raise.get("seat", -1)) != int(hero_seat) and int(to_call) > 0:
        if (position == "BTN" and len(raises) == 1
                and any(int(event.get("seat", -1)) == int(hero_seat)
                        and event.get("action") == "call" for event in active)):
            return "VS_LIMP_RAISE"
        return {1: "VS_OPEN", 2: "VS_3BET", 3: "VS_4BET"}.get(
            len(raises), "VS_4BET_PLUS")
    if last is not None and int(last.get("seat", -1)) == int(hero_seat):
        return {1: "HERO_OPENED", 2: "HERO_3BET"}.get(len(raises), "HERO_4BET_PLUS")
    return {1: "VS_OPEN", 2: "VS_3BET", 3: "VS_4BET"}.get(
        len(raises), "VS_4BET_PLUS")


def _preflop_prices(context, history, bb, depth):
    raises = [event for event in history
              if event.get("phase", "preflop") == "preflop"
              and event.get("action") in {"raise", "all_in"}]
    values = [max(0, int(event.get("amount", 0))) for event in raises]
    open_to = values[0] if values else 0
    three_to = values[1] if len(values) > 1 else 0
    four_to = values[2] if len(values) > 2 else 0
    price_key = (f"{context}:D{depth:g}:O{open_to}:"
                 f"3B{three_to}:4B{four_to}")
    if context == "VS_OPEN":
        off_grid = round(open_to / max(1, bb), 4) not in {2.0, 2.25, 2.5, 2.6}
    elif context == "VS_3BET":
        off_grid = round(three_to / max(1, bb), 4) not in {7.0, 8.0, 9.0, 10.0}
    elif context == "VS_4BET":
        expected = round(2.2 * three_to)
        off_grid = abs(four_to - expected) > 1
    else:
        off_grid = False
    return price_key, (open_to, three_to, four_to), off_grid


def _node_id(street, position, history, to_call, hero_seat):
    if street == "preflop":
        context = _preflop_context(position, hero_seat, history, to_call)
        branch = context
        return f"PREFLOP_{position}_{branch}"
    current = [e for e in history if e.get("phase") == street]
    aggression = sum(e.get("action") in {"raise", "all_in"} for e in current)
    branch = "VS_BET" if to_call else "FREE"
    if aggression > 1:
        branch += "_RE_RAISED"
    return f"{street.upper()}_{position}_{branch}"


def capture(raw, big_blind=10, hand_start_stacks=None):
    history = tuple(dict(event) for event in raw.action_history)
    posted_bb = [int(event.get("amount", 0)) for event in history
                 if event.get("action") == "post_big_blind"]
    bb = max(1, int(big_blind), max(posted_bb, default=0))
    current = _seat_amounts(history, raw.phase)
    historical_hero_bet = current.get(int(raw.your_seat), 0)
    historical_villain_bet = current.get(1 - int(raw.your_seat), 0)
    hero_bet = historical_hero_bet
    villain_bet = historical_villain_bet
    villain_bet = max(villain_bet, hero_bet + int(raw.to_call))
    hero_bet = max(hero_bet, villain_bet - int(raw.to_call))
    villain_stack = max(0, int(raw.opponent_stacks[0]))
    starts = hand_start_stacks
    if starts is None:
        starts = (int(raw.your_stack) + hero_bet,
                  villain_stack + villain_bet)
    starts = tuple(int(value) for value in starts)
    if len(starts) != 2:
        starts = (int(raw.your_stack) + hero_bet, villain_stack + villain_bet)
    pending = max(0, max(hero_bet, villain_bet) - min(hero_bet, villain_bet))
    pot = max(0, int(raw.pot))
    ledger_status = "consistent"
    active_events = [(index, event) for index, event in enumerate(history)
                     if event.get("phase", "preflop") == raw.phase
                     and not str(event.get("action", "")).startswith("post_")]
    historical_to_call = max(0, historical_villain_bet - historical_hero_bet)
    if not active_events and int(raw.to_call) != historical_to_call:
        ledger_status = "incomplete_history"
    elif active_events:
        last_index, last_event = active_events[-1]
        if (last_event.get("action") in {"raise", "all_in"}
                and int(last_event.get("seat", -1)) != int(raw.your_seat)):
            before = _seat_amounts(history[:last_index], raw.phase)
            hero_before = before.get(int(raw.your_seat), 0)
            amount = max(0, int(last_event.get("amount", 0)))
            # SDK fixtures in the wild use both raise-to and increment-style
            # history amounts. Accept either only within one current BB; a
            # large disagreement is an authority/history price conflict.
            candidates = {amount, max(0, amount - hero_before)}
            if min(abs(int(raw.to_call) - value) for value in candidates) > bb:
                ledger_status = "conflict"
    if int(raw.to_call) > int(raw.your_stack) or int(raw.min_raise) > int(raw.max_raise):
        ledger_status = "conflict"
    ledger = V2Ledger(pot, max(0, pot - pending), pending, 0, hero_bet,
                      villain_bet, ledger_status, "sdk_authority+action_history")
    position = "BTN" if int(raw.your_seat) == int(raw.dealer_seat) else "BB"
    preflop_context = _preflop_context(position, int(raw.your_seat), history, raw.to_call)
    active_preflop = [e for e in history
                      if e.get("phase", "preflop") == "preflop"
                      and not str(e.get("action", "")).startswith("post_")]
    last_active = active_preflop[-1] if active_preflop else None
    facing_opponent_bet = bool(
        raw.phase == "preflop" and int(raw.to_call) > 0 and last_active is not None
        and int(last_active.get("seat", -1)) != int(raw.your_seat)
        and last_active.get("action") in {"raise", "all_in", "bet"}
    )
    effective = min(int(raw.your_stack) + hero_bet, villain_stack + villain_bet)
    depth = round(effective / bb, 3)
    legal = tuple(sorted(str(action) for action in raw.valid_actions))
    line = tuple(f"{event.get('phase', 'preflop')}:{event.get('seat')}:"
                 f"{event.get('action')}:{event.get('amount', 0)}" for event in history)
    node_id = _node_id(raw.phase, position, history, int(raw.to_call), int(raw.your_seat))
    price_key, prices, off_grid = _preflop_prices(
        preflop_context, history, bb, depth) if raw.phase == "preflop" else ("", (0, 0, 0), False)
    key = NodeKey(raw.phase, node_id, position, depth, bb, _antes(history), starts,
                  pot, int(raw.to_call), hero_bet, villain_bet, int(raw.min_raise),
                  int(raw.max_raise), legal, line, "sdk_authority+action_history",
                  price_key, prices[0], prices[1], prices[2], ledger_status, off_grid)
    return V2Snapshot(
        int(raw.hand_number), str(raw.phase), tuple(map(str, raw.hole_cards)),
        tuple(map(str, raw.board)), int(raw.your_seat), int(raw.dealer_seat), position,
        pot, min(int(raw.to_call), int(raw.your_stack)), int(raw.your_stack),
        villain_stack, depth, bb, _antes(history), starts, hero_bet, villain_bet,
        int(raw.min_raise), int(raw.max_raise), legal, history, preflop_context,
        facing_opponent_bet, node_id, ledger, key, price_key,
        tuple((name, value) for name, value in zip(("open_to", "three_bet_to", "four_bet_to"), prices)),
        off_grid)

