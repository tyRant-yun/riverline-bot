from collections import Counter

from hu_bot.v2_runtime import V2Runtime
from tools.v2_preflop_1326_audit import COMBOS, run_row


def test_v221_continuous_3bet_price_corridor_keeps_conditioned_call_mix():
    runtime = V2Runtime()
    runtime.reset("v2.2.1-test", 100, 2000)
    for index, three_to in enumerate((890, 900, 910, 1000)):
        rows = [run_row(runtime, "BTN_VS_3BET", combo, 100,
                        open_to=250, three_to=three_to,
                        hand_number=400000 + index * len(COMBOS) + offset)
                for offset, combo in enumerate(COMBOS)]
        assert Counter(row["action"]["action"] for row in rows) == Counter({
            "call": 400, "fold": 872, "raise": 54})
        assert all(row["legal"] and not row["fallback_reason"] for row in rows)
        assert sum(bool(row["off_grid"]) for row in rows) in {0, len(COMBOS)}
        by_class = {}
        for row in rows:
            by_class.setdefault(row["class"], set()).add(row["action"]["action"])
        assert by_class["65o"] == {"fold"}
        assert by_class["76o"] == {"fold"}
        assert by_class["97o"] == {"fold"}
        assert by_class["65s"] == {"call"}
        assert by_class["55"] == {"call"}


def test_v221_btn_3bet_4bet_core_is_narrow_and_explainable():
    runtime = V2Runtime()
    runtime.reset("v2.2.1-4bet-core-test", 100, 2000)
    wanted = {"A2s", "KJo", "77", "A4s", "A5s", "TT", "AKs", "AA"}
    rows = {}
    for index, combo in enumerate(COMBOS):
        row = run_row(runtime, "BTN_VS_3BET", combo, 100,
                      open_to=250, three_to=1000,
                      hand_number=410000 + index)
        if row["class"] in wanted and row["class"] not in rows:
            rows[row["class"]] = row
    assert rows["A2s"]["action"]["action"] == "call"
    assert rows["KJo"]["action"]["action"] == "call"
    assert rows["77"]["action"]["action"] == "call"
    assert rows["A4s"]["action"]["action"] == "raise"
    assert rows["A5s"]["action"]["action"] == "raise"
    assert rows["TT"]["action"]["action"] == "raise"
    assert rows["AKs"]["action"]["action"] == "raise"
    assert rows["AA"]["action"]["action"] == "raise"
