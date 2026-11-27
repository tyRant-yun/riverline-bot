from collections import Counter

from hu_bot.v2_runtime import V2Runtime
from tools.v2_preflop_1326_audit import COMBOS, run_row
from tools.v2_preflop_v222_audit import run_iso


def test_v222_natural_btn_first_in_closes_gray_range_without_changing_open_size():
    runtime = V2Runtime()
    runtime.reset("v2.2.2-natural-first-in", 100, 2200)
    rows = [run_row(runtime, "BTN_FIRST_IN", combo, 100, hand_number=500000 + index)
            for index, combo in enumerate(COMBOS)]

    assert Counter(row["action"]["action"] for row in rows) == Counter({
        "raise": 1052, "call": 46, "fold": 228,
    })
    assert all(row["legal"] and not row["fallback_reason"] for row in rows)
    assert {row["action"].get("amount") for row in rows
            if row["action"]["action"] == "raise"} == {250}
    limp_classes = {row["class"] for row in rows if row["action"]["action"] == "call"}
    assert {"J4o", "Q3o"} <= limp_classes
    assert {"T4o", "J3o", "J2o", "Q2o", "T2o", "T3o"}.isdisjoint(limp_classes)
    assert sum(row["class"] == "AA" and row["action"]["action"] == "call"
               for row in rows) == 1
    assert sum(row["class"] == "A5s" and row["action"]["action"] == "call"
               for row in rows) == 1


def test_v222_natural_limp_iso_is_price_aware_and_legal_at_3_5_6bb():
    first_runtime = V2Runtime()
    first_runtime.reset("v2.2.2-natural-limp-denominator", 100, 2200)
    first_rows = [run_row(first_runtime, "BTN_FIRST_IN", combo, 100,
                          hand_number=510000 + index)
                  for index, combo in enumerate(COMBOS)]
    limp_combos = [tuple(row["combo"]) for row in first_rows
                   if row["action"]["action"] == "call"]
    assert len(limp_combos) == 46

    expected = {
        300: {"call": 42, "raise": 4},
        500: {"call": 18, "fold": 24, "raise": 4},
        600: {"call": 11, "fold": 31, "raise": 4},
    }
    runtime = V2Runtime()
    runtime.reset("v2.2.2-natural-limp-iso", 100, 2200)
    for iso_to, action_counts in expected.items():
        rows = [run_iso(runtime, combo, iso_to, 520000 + iso_to + index)
                for index, combo in enumerate(limp_combos)]
        assert Counter(row["action"]["action"] for row in rows) == Counter(action_counts)
        assert all(row["legal"] and not row["fallback_reason"] for row in rows)
        assert all(row["context"] == "VS_LIMP_RAISE" and not row["off_grid"] for row in rows)
        assert all(row["ledger_status"] == "consistent" for row in rows)


def test_v222_off_grid_3bet_corridor_preserves_safe_action_mix():
    runtime = V2Runtime()
    runtime.reset("v2.2.2-off-grid-3bet", 100, 2200)
    for index, three_to in enumerate((640, 650, 690, 700, 800, 900, 1000, 1010, 1050, 1060)):
        rows = [run_row(runtime, "BTN_VS_3BET", combo, 100,
                        open_to=250, three_to=three_to,
                        hand_number=530000 + index * len(COMBOS) + offset)
                for offset, combo in enumerate(COMBOS)]
        assert Counter(row["action"]["action"] for row in rows) == Counter({
            "call": 400, "fold": 872, "raise": 54,
        })
        assert all(row["legal"] and not row["fallback_reason"] for row in rows)
        expected_off_grid = three_to not in {700, 800, 900, 1000}
        assert sum(bool(row["off_grid"]) for row in rows) == (
            len(COMBOS) if expected_off_grid else 0)
        by_class = {}
        for row in rows:
            by_class.setdefault(row["class"], set()).add(row["action"]["action"])
        assert by_class["97o"] == {"fold"}
        assert by_class["65o"] == {"fold"}
        assert by_class["65s"] == {"call"}
        assert by_class["KJo"] == {"call"}
        assert by_class["AA"] == {"raise"}
        assert by_class["AKs"] == {"raise"}


def test_v222_outside_corridor_3bet_boundary_is_explicitly_conservative():
    runtime = V2Runtime()
    runtime.reset("v2.2.2-off-grid-boundary", 100, 2200)
    for index, three_to in enumerate((590, 1110)):
        rows = [run_row(runtime, "BTN_VS_3BET", combo, 100,
                        open_to=250, three_to=three_to,
                        hand_number=540000 + index * len(COMBOS) + offset)
                for offset, combo in enumerate(COMBOS)]
        assert Counter(row["action"]["action"] for row in rows) == Counter({
            "fold": 1272, "raise": 54,
        })
        assert all(row["legal"] and not row["fallback_reason"] for row in rows)
        assert sum(bool(row["off_grid"]) for row in rows) == len(COMBOS)
        by_class = {}
        for row in rows:
            by_class.setdefault(row["class"], set()).add(row["action"]["action"])
        assert by_class["97o"] == {"fold"}
        assert by_class["65o"] == {"fold"}
        assert by_class["65s"] == {"fold"}
        assert by_class["AA"] == {"raise"}
        assert by_class["AKs"] == {"raise"}
