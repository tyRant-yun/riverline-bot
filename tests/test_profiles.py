from bot import HUBot
from hu_bot.memory import OpponentModel
from hu_bot.profiles import ProfileCache


def start(identity="other", name="Bot", match="one"):
    return {"match_id": match, "game_config": {"variant": "nlhe", "big_blind": 10},
            "seats": [{"seat": 0, "is_self": True, "participant_id": "self"},
                      {"seat": 1, "is_self": False, "participant_id": identity,
                       "display_name": name}]}


def model():
    result = OpponentModel()
    for _ in range(100):
        result.stat("fold_to_river_bet", .4).observe(True)
    return result


def test_identity_is_not_display_name_and_context_is_separate():
    cache = ProfileCache()
    assert cache.key(start("a", "Same")) != cache.key(start("b", "Same"))
    assert cache.key(start("a", "Name1")) == cache.key(start("a", "Name2"))
    assert cache.key(start(None)) is None
    assert cache.key({"seats": []}) is None
    altered = start()
    altered["game_config"]["big_blind"] = 20
    assert cache.key(altered) != cache.key(start())


def test_profile_capacity_expiry_and_no_raw_cards():
    time = [0.]
    cache = ProfileCache(max_profiles=2, max_bytes=1024, ttl_seconds=10, clock=lambda: time[0])
    for identity in ("a", "b", "c"):
        cache.save(cache.key(start(identity)), model())
    assert len(cache) == 2 and cache.byte_size <= 1024
    assert cache.get(cache.key(start("a"))) is None
    data = cache.get(cache.key(start("c")))
    assert set(data) == {"schema", "stats"}
    time[0] = 11
    assert len(cache) == 0


def test_old_profile_is_only_bounded_prior_not_fake_live_samples():
    cache = ProfileCache()
    key = cache.key(start())
    cache.save(key, model())
    fresh = OpponentModel()
    assert cache.apply_weak_prior(key, fresh) == 1
    stat = fresh.stats["fold_to_river_bet"]
    assert .4 < stat.prior <= .430000001
    assert stat.success == stat.opportunities == 0
    assert fresh.population_priors["fold_to_river_bet"] == .4
    assert fresh.range_adjustment("fold_to_river_bet") == 0
    assert cache.apply_weak_prior(key, OpponentModel(enabled=False)) == 0


def test_same_match_reconnect_preserves_learning_different_match_resets():
    bot = HUBot()
    bot.on_match_start(start())
    bot.engine.opponent = model()
    existing = bot.engine.opponent
    message = {**start(), "round_number": 3, "state": {"big_blind": 20}}
    bot.on_reconnected(message)
    assert bot.engine.opponent is existing and bot.engine.big_blind == 20
    bot.on_match_end({})
    bot.on_match_start(start(match="two"))
    assert bot.engine.opponent is not existing
    assert bot.engine.opponent.stats["fold_to_river_bet"].opportunities == 0
    bot.on_match_end({})
    bot.on_match_start(start("different", match="three"))
    assert not bot.engine.opponent.stats
