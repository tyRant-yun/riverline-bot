from hu_bot.v2_terminal import resolve_terminal


def test_standard_win_and_tie_are_relative_to_current_decision_point():
    win = resolve_terminal((60, 60), (40, 40), "hero_win", displayed_pot=80)
    assert win.final_stacks == (140, 60)
    assert win.q_chip == 80
    assert win.conserved

    tie = resolve_terminal((60, 60), (40, 40), "tie", displayed_pot=80)
    assert tie.final_stacks == (100, 100)
    assert tie.q_chip == 40


def test_uncalled_wager_returns_before_unequal_all_in_distribution():
    result = resolve_terminal((0, 60), (100, 40), "hero_win",
                               uncalled_seat=0, uncalled_amount=60,
                               displayed_pot=140)
    assert result.contestable_pot == 80
    assert result.returned_uncalled == 60
    assert result.final_stacks == (140, 60)
    assert result.q_chip == 140


def test_fold_and_odd_chip_are_conserved():
    folded = resolve_terminal((59, 60), (41, 40), "fold", folded_seat=1,
                               displayed_pot=81)
    assert folded.payouts == (81, 0)
    assert folded.final_stacks == (140, 60)

    tie = resolve_terminal((59, 60), (41, 40), "tie", odd_chip_seat=1,
                           displayed_pot=81)
    assert tie.payouts == (40, 41)
    assert tie.final_stacks == (99, 101)
    assert tie.conserved
