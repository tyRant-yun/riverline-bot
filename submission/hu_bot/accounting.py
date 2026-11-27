"""Immutable rule, decision, and cash-flow adapters for the SDK state.

The runtime strategy still consumes the frozen ``NormalizedState`` contract.  This
module is deliberately an adapter layer: it keeps the wire values and the replayed
history separate, records disagreements instead of repairing history in place, and
exposes only conservative derived cash-flow values to callers.
"""
from dataclasses import asdict, dataclass, replace
from typing import Any, Mapping


SOURCE_PLATFORM = "platform_verified"
SOURCE_ASSUMED = "assumed_for_research"
SOURCE_UNKNOWN = "unknown"
SOURCES = {SOURCE_PLATFORM, SOURCE_ASSUMED, SOURCE_UNKNOWN}


def _freeze(value: Any) -> Any:
    """Recursively freeze JSON-like values for deep immutable snapshots."""
    if isinstance(value, Mapping):
        return tuple(sorted((str(k), _freeze(v)) for k, v in value.items()))
    if isinstance(value, (list, tuple)):
        return tuple(_freeze(v) for v in value)
    if isinstance(value, set):
        return tuple(sorted(_freeze(v) for v in value))
    return value


def _raw_dict(value: tuple[tuple[str, Any], ...]) -> dict[str, Any]:
    return {key: item for key, item in value}


def _get(obj: Any, key: str, default: Any = None) -> Any:
    if isinstance(obj, Mapping):
        return obj.get(key, default)
    return getattr(obj, key, default)


def _seat_values(values: Any, seat_count: int, *, hero_seat: int = 0,
                 hero_value: int | None = None) -> tuple[int | None, ...]:
    if isinstance(values, Mapping):
        return tuple(values.get(i) for i in range(seat_count))
    if isinstance(values, (list, tuple)):
        return tuple(values[i] if i < len(values) else None for i in range(seat_count))
    if hero_value is not None and seat_count == 2:
        return (hero_value, None) if hero_seat == 0 else (None, hero_value)
    return tuple(None for _ in range(seat_count))


@dataclass(frozen=True, slots=True)
class RuleSnapshot:
    """Rules known at the match boundary, with explicit provenance."""

    match_id: str
    variant: str | None
    starting_stack: int | None
    small_blind: int | None
    big_blind: int | None
    ante: int | None
    num_players: int | None
    blind_schedule: tuple[Any, ...]
    source: str
    schedule_source: str
    rule_version: str
    evidence_uri: str | None = None

    def __post_init__(self) -> None:
        if self.source not in SOURCES or self.schedule_source not in SOURCES:
            raise ValueError("invalid rule provenance")

    @classmethod
    def unknown(cls, match_id: str = "offline", big_blind: int | None = None) -> "RuleSnapshot":
        return cls(match_id, None, None, None, big_blind, None, None, (),
                   SOURCE_UNKNOWN, SOURCE_UNKNOWN, "unknown")

    @classmethod
    def from_config(cls, match_id: str, config: Mapping[str, Any] | None,
                    *, source: str = SOURCE_PLATFORM,
                    evidence_uri: str | None = None) -> "RuleSnapshot":
        config = config or {}
        values = {key: config.get(key) for key in
                  ("variant", "starting_stack", "small_blind", "big_blind", "ante",
                   "num_players")}
        schedule = config.get("blind_schedule")
        schedule_source = source if schedule is not None else SOURCE_UNKNOWN
        if source not in SOURCES:
            raise ValueError("invalid rule provenance")
        return cls(str(match_id), values["variant"], values["starting_stack"],
                   values["small_blind"], values["big_blind"], values["ante"],
                   values["num_players"], _freeze(schedule or ()), source,
                   schedule_source, str(config.get("rule_version", "unknown")),
                   evidence_uri)

    def as_dict(self) -> dict[str, Any]:
        result = asdict(self)
        result["blind_schedule"] = list(result["blind_schedule"])
        return result

    def with_observed_big_blind(self, big_blind: int,
                                *, source: str = SOURCE_PLATFORM) -> "RuleSnapshot":
        """Return a new snapshot for a verified per-hand blind observation."""
        if int(big_blind) <= 0 or source not in SOURCES:
            raise ValueError("invalid observed big blind")
        return replace(self, big_blind=int(big_blind), source=source)

    def chips_to_bb(self, chips: int | float) -> float | None:
        """Convert a chip amount using this snapshot's observed blind level."""
        if self.big_blind is None or self.big_blind <= 0:
            return None
        return float(chips) / self.big_blind


@dataclass(frozen=True, slots=True)
class RawActionEvent:
    index: int
    seat: int
    action: str
    phase: str
    amount: int
    is_timeout: bool
    raw: tuple[tuple[str, Any], ...]


@dataclass(frozen=True, slots=True)
class LedgerEvent:
    """One raw event plus its non-authoritative replay interpretation."""

    index: int
    seat: int
    action: str
    phase: str
    raw_amount: int
    raise_to: int | None
    incremental: int
    own_before: int
    table_before: int
    table_after: int
    matched_increment: int
    potential_return_after: int
    pot_before: int
    pot_after: int
    is_timeout: bool
    amount_semantics: str
    raw: tuple[tuple[str, Any], ...]


@dataclass(frozen=True, slots=True)
class BettingLedger:
    """Replayed commitments; authoritative fields remain separate."""

    events: tuple[LedgerEvent, ...]
    current_street: str
    committed_by_seat: tuple[int, ...]
    street_bets_by_seat: tuple[int, ...]
    reconstructed_pot: int
    authoritative_pot: int | None
    contestable_pot: int
    potential_uncalled: int
    authoritative_to_call: int | None
    replay_to_call: int
    hand_start_stacks: tuple[int | None, ...]
    current_stacks: tuple[int | None, ...]
    payouts: tuple[tuple[int, int], ...]
    refunds: tuple[tuple[int, int], ...]
    status: str
    conflicts: tuple[str, ...]
    source: str

    @property
    def consistent(self) -> bool:
        return self.status == "consistent"

    @property
    def strategy_safe(self) -> bool:
        """Only a fully matching ledger may unlock the existing price-gated table."""
        return self.consistent and not self.conflicts

    def summary(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "conflicts": list(self.conflicts),
            "reconstructed_pot": self.reconstructed_pot,
            "authoritative_pot": self.authoritative_pot,
            "contestable_pot": self.contestable_pot,
            "potential_uncalled": self.potential_uncalled,
            "replay_to_call": self.replay_to_call,
            "authoritative_to_call": self.authoritative_to_call,
            "committed_by_seat": list(self.committed_by_seat),
            "street_bets_by_seat": list(self.street_bets_by_seat),
            "hand_start_stacks": list(self.hand_start_stacks),
            "current_stacks": list(self.current_stacks),
            "payouts": [list(item) for item in self.payouts],
            "refunds": [list(item) for item in self.refunds],
        }


@dataclass(frozen=True, slots=True)
class DecisionSnapshot:
    """Deep immutable SDK decision state and its bounded accounting view."""

    match_id: str
    round_id: str
    request_id: str
    hand_number: int
    phase: str
    hero_seat: int
    dealer_seat: int
    hole_cards: tuple[str, ...]
    board: tuple[str, ...]
    seat_stacks: tuple[int | None, ...]
    hand_start_stacks: tuple[int | None, ...]
    authority_pot: int | None
    to_call: int | None
    min_raise: int | None
    max_raise: int | None
    valid_actions: tuple[str, ...]
    raw_events: tuple[RawActionEvent, ...]
    ledger: BettingLedger
    rules: RuleSnapshot
    status: str
    conflicts: tuple[str, ...]

    @classmethod
    def from_game_state(cls, state: Any, *, rules: RuleSnapshot | None = None,
                        hand_start_stacks: Any = None) -> "DecisionSnapshot":
        hero = int(_get(state, "your_seat", 0))
        opponent = tuple(int(v) for v in (_get(state, "opponent_stacks", ()) or ()))
        seat_count = max(2, hero + 1, len(opponent) + 1)
        stacks: list[int | None] = [None] * seat_count
        stacks[hero] = int(_get(state, "your_stack", 0))
        other_seats = [seat for seat in range(seat_count) if seat != hero]
        for seat, value in zip(other_seats, opponent):
            stacks[seat] = value
        starts = _seat_values(hand_start_stacks, seat_count, hero_seat=hero)
        raw_history = tuple(_get(state, "action_history", ()) or ())
        raw_events = tuple(RawActionEvent(
            i, int(_get(event, "seat", 0)), str(_get(event, "action", "")),
            str(_get(event, "phase", "preflop")), int(_get(event, "amount", 0)),
            bool(_get(event, "is_timeout", False)),
            tuple(sorted((str(k), _freeze(v)) for k, v in event.items()))
            if isinstance(event, Mapping) else ())
            for i, event in enumerate(raw_history))
        rules = rules or RuleSnapshot.unknown(
            str(_get(state, "match_id", "offline")),
            int(_get(state, "big_blind", 0) or 0) or None)
        ledger = build_ledger(
            raw_events, current_street=str(_get(state, "phase", "preflop")),
            authoritative_pot=int(_get(state, "pot", 0)),
            authoritative_to_call=int(_get(state, "to_call", 0)),
            hero_seat=hero, hand_start_stacks=starts, current_stacks=tuple(stacks),
            seat_count=seat_count)
        conflicts = list(ledger.conflicts)
        min_raise = int(_get(state, "min_raise", 0))
        max_raise = int(_get(state, "max_raise", 0))
        if min_raise < 0 or max_raise < 0 or (max_raise and min_raise > max_raise):
            conflicts.append("authority:invalid_raise_bounds")
        if conflicts != list(ledger.conflicts):
            ledger = replace(ledger, status="inconsistent",
                             conflicts=tuple(dict.fromkeys(conflicts)))
        conflicts = ledger.conflicts
        return cls(
            str(_get(state, "match_id", rules.match_id) or rules.match_id),
            str(_get(state, "round_id", "")), str(_get(state, "request_id", "")),
            int(_get(state, "hand_number", 0)), str(_get(state, "phase", "preflop")),
            hero, int(_get(state, "dealer_seat", 0)),
            tuple(str(v) for v in (_get(state, "hole_cards", ()) or ())),
            tuple(str(v) for v in (_get(state, "board", ()) or ())), tuple(stacks), starts,
            int(_get(state, "pot", 0)), int(_get(state, "to_call", 0)),
            int(_get(state, "min_raise", 0)), int(_get(state, "max_raise", 0)),
            tuple(str(v) for v in (_get(state, "valid_actions", ()) or ())), raw_events,
            ledger, rules, ledger.status, conflicts)

    def summary(self) -> dict[str, Any]:
        return {
            "match_id": self.match_id,
            "round_id": self.round_id,
            "request_id": self.request_id,
            "hand_number": self.hand_number,
            "phase": self.phase,
            "hero_seat": self.hero_seat,
            "authority": {"pot": self.authority_pot, "to_call": self.to_call,
                           "min_raise": self.min_raise, "max_raise": self.max_raise},
            "status": self.status,
            "conflicts": list(self.conflicts),
            "ledger": self.ledger.summary(),
            "rules": self.rules.as_dict(),
        }


def _parse_event(event: RawActionEvent, own: int, table: int) -> tuple[int, int, int | None, str]:
    """Return target street bet, increment, raise-to, and semantics."""
    amount = max(0, int(event.amount))
    action = event.action
    owed = max(0, table - own)
    if action == "call":
        if amount == 0:
            return table, owed, None, "call_implicit_or_missing_amount"
        if amount == table and amount != owed:
            return table, owed, None, "call_total"
        if amount <= owed:
            return min(table, own + amount), amount, None, "call_increment"
        return table, owed, None, "call_clamped_to_owed"
    if action in {"raise", "all_in"}:
        target = max(own, amount)
        return target, max(0, target-own), target, "raise_to"
    if action in {"post_small_blind", "post_big_blind"}:
        target = max(own, amount)
        return target, max(0, target-own), None, "blind_total"
    if action == "post_ante":
        return own, amount, None, "ante_increment"
    return own, 0, None, "non_committing"


def build_ledger(raw_events: tuple[RawActionEvent, ...], *, current_street: str,
                 authoritative_pot: int | None, authoritative_to_call: int | None,
                 hero_seat: int, hand_start_stacks: tuple[int | None, ...],
                 current_stacks: tuple[int | None, ...], seat_count: int) -> BettingLedger:
    committed = [0] * seat_count
    street_bets = [0] * seat_count
    events: list[LedgerEvent] = []
    pot = 0
    active_street = "preflop"
    conflicts: list[str] = []
    for event in raw_events:
        phase = event.phase or "preflop"
        if phase != active_street:
            active_street, street_bets = phase, [0] * seat_count
        if not 0 <= event.seat < seat_count:
            conflicts.append(f"event[{event.index}]:seat_out_of_range")
            continue
        own = street_bets[event.seat]
        table = max(street_bets, default=0)
        target, incremental, raise_to, semantics = _parse_event(event, own, table)
        if event.action == "post_ante":
            # Ante is dead money, not a street bet, but it is part of pot and total commitment.
            target = own
        before = pot
        committed[event.seat] += incremental
        pot += incremental
        street_bets[event.seat] = target
        minimum_commit = min(committed) if len(committed) == 2 else 0
        potential_return = max(committed, default=0) - minimum_commit
        matched_increment = max(0, min(committed[event.seat], sum(committed)-committed[event.seat])
                                - (committed[event.seat]-incremental))
        events.append(LedgerEvent(
            event.index, event.seat, event.action, phase, event.amount, raise_to,
            incremental, own, table, max(street_bets, default=0), matched_increment,
            potential_return, before, pot, event.is_timeout, semantics, event.raw))
    hero_bet = street_bets[hero_seat] if hero_seat < seat_count else 0
    other_bets = [street_bets[i] for i in range(seat_count) if i != hero_seat]
    replay_to_call = max(0, max(other_bets, default=0) - hero_bet)
    if authoritative_pot is not None and authoritative_pot != pot:
        conflicts.append(f"pot_history_gap:{pot}->{authoritative_pot}")
    if authoritative_to_call is not None and authoritative_to_call != replay_to_call:
        conflicts.append(f"to_call_history_gap:{replay_to_call}->{authoritative_to_call}")
    for i, (start, current) in enumerate(zip(hand_start_stacks, current_stacks)):
        if start is not None and current is not None and start < current + committed[i]:
            conflicts.append(f"cashflow[{i}]:committed_exceeds_start")
    contestable = 2 * min(committed) if len(committed) == 2 else sum(committed)
    potential_uncalled = max(0, sum(committed) - contestable)
    status = "consistent" if not conflicts else "inconsistent"
    return BettingLedger(
        tuple(events), current_street, tuple(committed), tuple(street_bets), pot,
        authoritative_pot, contestable, potential_uncalled, authoritative_to_call,
        replay_to_call, tuple(hand_start_stacks), tuple(current_stacks), (), (), status,
        tuple(dict.fromkeys(conflicts)), SOURCE_ASSUMED if raw_events else SOURCE_UNKNOWN)


def settlement_ledger(ledger: BettingLedger, result: Mapping[str, Any]) -> BettingLedger:
    """Attach explicit payout/refund facts without inferring platform refunds."""
    payouts = tuple((int(item["seat"]), int(item["amount"]))
                    for item in result.get("payouts", ()) if "seat" in item and "amount" in item)
    raw_refunds = result.get("uncalled_refunds")
    refunds = tuple((i, int(value)) for i, value in enumerate(raw_refunds or ()) if int(value))
    conflicts = list(ledger.conflicts)
    if raw_refunds is None and ledger.potential_uncalled:
        conflicts.append("platform_refund_unknown")
    return BettingLedger(
        ledger.events, ledger.current_street, ledger.committed_by_seat,
        ledger.street_bets_by_seat, ledger.reconstructed_pot, ledger.authoritative_pot,
        ledger.contestable_pot, ledger.potential_uncalled, ledger.authoritative_to_call,
        ledger.replay_to_call, ledger.hand_start_stacks, ledger.current_stacks, payouts,
        refunds, "consistent" if not conflicts else "inconsistent",
        tuple(dict.fromkeys(conflicts)), ledger.source)


class MatchEventDeduper:
    """Match-local, non-evicting hand identity guard for reconnect/replay hooks."""

    def __init__(self, match_id: str = "") -> None:
        self.match_id = str(match_id)
        self._seen_round_ids: set[str] = set()
        self._seen_hand_numbers: set[int] = set()

    def reset(self, match_id: str) -> None:
        self.match_id = str(match_id)
        self._seen_round_ids.clear()
        self._seen_hand_numbers.clear()

    def first_result(self, round_id: str | None, hand_number: int | None) -> bool:
        """Return true exactly once for a hand in this match.

        ``round_id`` is preferred because it survives reconnects.  Some old
        result hooks omit it, so hand number is retained as a bounded fallback;
        there is intentionally no rolling eviction that could re-count hand 700+.
        """
        rid = str(round_id or "")
        hand = int(hand_number or 0)
        if rid:
            if rid in self._seen_round_ids:
                return False
            self._seen_round_ids.add(rid)
            if hand:
                self._seen_hand_numbers.add(hand)
            return True
        if hand and hand in self._seen_hand_numbers:
            return False
        if hand:
            self._seen_hand_numbers.add(hand)
        return True

    @property
    def seen_count(self) -> int:
        return len(self._seen_round_ids) + len(self._seen_hand_numbers)
