"""Bounded process-local opponent summaries; no files or network persistence."""
from collections import OrderedDict
import hashlib
import json
from time import monotonic


class ProfileCache:
    def __init__(self, max_profiles=16, max_bytes=128*1024, ttl_seconds=6*3600,
                 clock=monotonic):
        self.max_profiles = max_profiles
        self.max_bytes = max_bytes
        self.ttl_seconds = ttl_seconds
        self.clock = clock
        self._entries = OrderedDict()

    @staticmethod
    def key(message):
        seats = message.get("seats", [])
        others = [s for s in seats if s.get("is_self") is False]
        selves = [s for s in seats if s.get("is_self") is True]
        if len(seats) != 2 or len(others) != 1 or len(selves) != 1:
            return None
        identity = others[0].get("participant_id")
        if not isinstance(identity, str) or not identity or len(identity) > 256:
            return None
        config = message.get("game_config", {})
        # Separate incompatible starting contexts; never identify bots by display name.
        context = [config.get("variant", "nlhe"), config.get("starting_stack"),
                   config.get("big_blind"), config.get("ante"), identity]
        return hashlib.sha256(json.dumps(context, separators=(",", ":")).encode()).hexdigest()

    @property
    def byte_size(self):
        return sum(len(key.encode()) + len(payload) for key, (_, payload) in self._entries.items())

    def __len__(self):
        self._expire()
        return len(self._entries)

    def _expire(self):
        now = self.clock()
        for key, (saved, _) in list(self._entries.items()):
            if now - saved >= self.ttl_seconds:
                del self._entries[key]

    def get(self, key):
        self._expire()
        if key not in self._entries:
            return None
        self._entries.move_to_end(key)
        return json.loads(self._entries[key][1])

    def save(self, key, model):
        if key is None:
            return
        rows = []
        for name, stat in sorted(model.stats.items(), key=lambda item: -item[1].opportunities):
            if stat.opportunities < 8 or len(name) > 128:
                continue
            rows.append({"key": name, "base_prior": model.population_priors.get(name, stat.prior),
                         "success": stat.success, "opportunities": stat.opportunities})
            if len(rows) == 128:
                break
        if not rows:
            return
        payload = b""
        while rows:
            payload = json.dumps({"schema": 1, "stats": rows}, separators=(",", ":")).encode()
            if len(payload) <= 8192 and len(payload) + len(key) <= self.max_bytes:
                break
            rows.pop()
        if not rows:
            return
        self._expire()
        self._entries[key] = (self.clock(), payload)
        self._entries.move_to_end(key)
        while len(self._entries) > self.max_profiles or self.byte_size > self.max_bytes:
            self._entries.popitem(last=False)

    def apply_weak_prior(self, key, model):
        profile = self.get(key)
        if profile is None or not model.enabled:
            return 0
        count = 0
        for row in profile["stats"]:
            base = row["base_prior"]
            # Current-match opportunity counters remain zero. Old observations
            # cannot on their own satisfy the live 12/20/24/48 evidence gates.
            estimate = (20*base + row["success"]) / (20 + row["opportunities"])
            shift = max(-.03, min(.03, .20*(estimate-base)))
            stat = model.stat(row["key"], prior=base)
            stat.prior = max(0., min(1., base + shift))
            count += 1
        return count
