"""What makes a cell precise enough to anchor a short row run.

`detect_short_row_reuse` matches a 3-11 column identical / scaled run between two
data rows. Which cells may join a run was decided by SIGNIFICANT FIGURES, which
mixes a value's magnitude into a judgement about its recorded precision: two cells
written to the same number of decimals get opposite verdicts depending on whether
they happen to sit above or below ten. One rejected cell in the middle of a run
splits it below the 3-column floor, and the whole relation disappears.

The gate used to decide something else as well -- which of the detector's two
passes judges a pair -- so relaxing it moved pairs across that boundary and changed
their verdict. The last cases pin that the two questions are now asked separately.

All data is synthetic; the layouts (a labelled two-group panel, two adjacent
condition rows) are ordinary supplementary shapes, not any paper's numbers.

Signal, not verdict: a reported run is a data inconsistency for the authors to
explain, never an accusation.
"""
from __future__ import annotations

from paperconan._audit import (
    _can_pin_a_ratio, _is_short_hp, detect_short_row_reuse,
)
from paperconan._sheet import Sheet

K = 0.8409          # an arbitrary constant, not a power of ten

# One value below ten, so at three decimals it carries 4 significant figures while
# its neighbours carry 5. Every cell is recorded to the same precision.
SPANS_TEN = [42.137, 58.914, 9.376, 71.205, 33.842]
# The same shape kept entirely above ten, so every cell reaches 5 significant
# figures at three decimals. This is the control: only the magnitudes differ.
ABOVE_TEN = [42.137, 58.914, 19.376, 71.205, 33.842]


def _two_group_panel(base, decimals=3):
    """Two labelled groups, one row each, where group B == K * group A.

    Both rows are stored at the panel's own display precision -- what a
    supplementary sheet actually contains after a copy-then-scale is written out
    and re-rounded.
    """
    a = [round(v, decimals) for v in base]
    b = [round(v * K, decimals) for v in a]
    return Sheet.from_rows([
        ["", "Rep 1", "Rep 2", "Rep 3", "Rep 4", "Rep 5"],
        ["Group A", None, None, None, None, None],
        ["Vehicle", *a],
        ["Group B", None, None, None, None, None],
        ["Treated", *b],
    ])


def _scaled(sheet):
    findings = detect_short_row_reuse({("sd.xlsx", "Figure 4b"): sheet})
    return [f for f in findings if f["kind"] == "scaled_row_reuse"]


def test_a_scaled_run_is_found_when_every_cell_clears_five_significant_figures():
    """The control case, and the behaviour that must not regress."""
    scaled = _scaled(_two_group_panel(ABOVE_TEN))

    assert len(scaled) == 1, f"expected the {K} run, got {scaled}"
    assert abs(scaled[0]["ratio"] - K) < 1e-3
    assert scaled[0]["run_length"] == 5


def test_one_value_below_ten_does_not_hide_the_same_scaled_run():
    """Only the magnitude of one cell differs from the control above.

    Precision is a property of how the number was recorded, not of how large it
    is, so the run must survive a cell that happens to sit below ten.
    """
    scaled = _scaled(_two_group_panel(SPANS_TEN))

    assert len(scaled) == 1, (
        f"a 5-column scaled run vanished because one cell sits below ten: {scaled}"
    )
    assert abs(scaled[0]["ratio"] - K) < 1e-3
    assert scaled[0]["run_length"] == 5, (
        "the run was split by the cell below ten rather than kept whole"
    )


# --- the gate must not decide which PASS judges a pair ------------------------
#
# An adjacent pair is judged by pass 1 when it can be read from either side, and
# by pass 2 when only the dividend is precise enough to pin the ratio. Pass 1 drops
# every same-band pair; pass 2 asks the finer question -- does the ratio cover a
# sub-range (a copied segment) or the whole row (a step along a fitted curve)?
#
# Ownership used to be decided by the run gate itself, so widening what may join a
# run silently moved pairs across that boundary and flipped their verdict. The two
# cases below pin both halves.

ADJACENT_DIVISOR = [4.213, 5.768, 3.194, 6.805, 2.547, 9.011, 3.322, 7.140]


def _adjacent_pair(divisor, dividend):
    return Sheet.from_rows([["cond_A", *divisor], ["cond_B", *dividend]])


def test_an_adjacent_partial_ratio_survives_the_divisor_joining_the_candidate_pool():
    """A sub-range copied from the row above, with a divisor that is precise but small.

    Every divisor cell sits below ten, so at three decimals each is granular to
    more than the tolerance a run is judged at -- it cannot pin the ratio alone,
    and the precise dividend does it. That is pass 2's case whether or not the row
    also clears the run gate, so relaxing the run gate must not take it away.

    This case is why pass ownership is asked in relative units rather than through
    `_is_short_hp`. Tie the two together again and the run is silently lost.
    """
    dividend = ([round(v * 1.17, 6) for v in ADJACENT_DIVISOR[:5]]
                + [88.5314, 12.8873, 44.1962])

    scaled = _scaled(_adjacent_pair(ADJACENT_DIVISOR, dividend))

    assert len(scaled) == 1, f"the copied sub-range was dropped: {scaled}"
    assert abs(scaled[0]["ratio"] - 1.17) < 1e-3
    assert scaled[0]["run_length"] == 5


def test_one_row_pair_yields_one_finding_across_both_passes():
    """Two passes, one pair, one finding -- and the helpers above cannot see this.

    Pass 1 picks a single relation per pair through its arm preference. Pass 2 can
    reach an adjacent pair that pass 1 already reported, because the band test does
    not suppress pass 1's IDENTICAL arm, and describe a scaled run over different
    columns of the same two rows. Both come back at `high`, with the same block on
    each side, each taking a slot of the finding cap, and a reader sees two entries
    about one pair of rows.

    The rows here carry a verbatim run in their first three columns and a separate
    1.17x run in the next three, with a divisor that cannot pin. Every other case in
    this file filters to `scaled_row_reuse`, so a duplicate arriving through a
    different arm is invisible to all of them -- which is why this one counts the
    findings themselves.
    """
    divisor = [4.213, 5.768, 3.194, 6.805, 2.547, 9.011, 3.322, 7.140]
    dividend = (divisor[:3]
                + [round(v * 1.17, 6) for v in divisor[3:6]]
                + [88.5314, 12.8873])

    findings = detect_short_row_reuse(
        {("sd.xlsx", "Figure 4b"): _adjacent_pair(divisor, dividend)})

    assert len(findings) == 1, (
        "one row pair produced "
        f"{[(f['kind'], f['run_length']) for f in findings]}"
    )


def test_a_pair_neither_row_can_pin_is_not_reported_at_all():
    """Pass 2's premise is that ONE side pins the ratio. Neither side here does.

    Both rows are recorded to three decimals with every value below ten, so each
    cell is granular to more than the tolerance the run is judged at. Nothing holds
    the constant, and a 3-column agreement between two grids that coarse is the
    chance match the precision floor exists to exclude.

    It is also what keeps the pair from being read twice. Ownership needs the
    ASYMMETRY, not just "the divisor cannot pin": drop the requirement that the
    dividend can, and this one relation comes back from both ends at once -- as
    k = 1.17 over 3 columns and as 1/k = 0.8547 over 4, which a reader sees as two
    separate findings about two separate things.
    """
    both_coarse = ([round(v * 1.17, 3) for v in ADJACENT_DIVISOR[:5]]
                   + [8.853, 1.288, 4.419])

    assert _scaled(_adjacent_pair(ADJACENT_DIVISOR, both_coarse)) == [], (
        "a ratio no cell in either row is precise enough to pin was reported"
    )


def test_a_pair_either_row_could_pin_stays_with_pass_1():
    """The other side of the ownership rule, and the one that keeps pass 2 narrow.

    Both rows here are precise enough to pin the ratio unaided, so the pair can be
    read from either end and there is no asymmetry for pass 2 to own. It belongs to
    pass 1, which drops same-band pairs -- deliberately, because these are the
    commonest adjacent shape there is. Letting pass 2 take them is what took the
    local corpus from 312 findings to 460 with sheets pinned at the result cap.
    """
    precise = [42.137251, 58.914003, 31.940882, 68.051177,
               25.470664, 90.113925, 33.220518, 71.402336]
    dividend = ([round(v * 1.17, 6) for v in precise[:5]]
                + [88.531400, 12.887300, 44.196200])

    assert _scaled(_adjacent_pair(precise, dividend)) == [], (
        "an adjacent pair both rows could pin was taken by the low-divisor pass"
    )

    # Whether a cell can pin is its step measured AGAINST the value, so a coarse
    # decimal count on a large number still pins: 421.372 moves in steps of 0.001,
    # which is 2.4e-6 of itself, far inside the tolerance. Judge it on decimals
    # alone and this row reads as unable to pin, handing the pair to pass 2 -- the
    # same absolute-versus-relative confusion the run gate was just cured of, moved
    # one predicate over.
    coarse_but_large = [421.372, 588.914, 319.408, 680.512,
                        254.707, 901.139, 332.205, 714.023]
    large_dividend = ([round(v * 1.17, 6) for v in coarse_but_large[:5]]
                      + [885.314, 128.873, 441.962])

    assert _scaled(_adjacent_pair(coarse_but_large, large_dividend)) == [], (
        "a large-magnitude 3-decimal row was treated as unable to pin a ratio"
    )


def test_a_row_that_repeats_one_precise_value_cannot_pin_a_ratio():
    """Pinning needs distinct values, not merely precise ones.

    This divisor carries three cells precise enough to pin, but they are all the
    SAME number -- a plateau, which fixes no ratio anywhere else in the row. So the
    row cannot pin, the pair is pass 2's, and the run over the coarse part of the
    row is reported. Count the precise cells without asking how many are distinct
    and the row reads as self-sufficient, pass 2 declines the pair, and pass 1
    drops it for being adjacent.
    """
    plateau_divisor = [12.3456, 12.3456, 12.3456,
                       4.213, 5.768, 3.194, 6.805, 2.547]
    dividend = [99.1234, 77.5678, 55.9012,
                round(4.213 * 1.17, 6), round(5.768 * 1.17, 6),
                round(3.194 * 1.17, 6), 88.5314, 12.8873]

    scaled = _scaled(_adjacent_pair(plateau_divisor, dividend))

    assert len(scaled) == 1, f"the run past the plateau was dropped: {scaled}"
    assert abs(scaled[0]["ratio"] - 1.17) < 1e-3


def test_an_adjacent_whole_row_ratio_is_still_read_as_a_curve_step():
    """The other half of pass 2's question, and the reason it is asked at all.

    When the ratio holds across the WHOLE row rather than a sub-range, two
    neighbouring rows are a step along a fitted curve or a global rescale -- benign,
    and the commonest shape in dose-response data. Whichever pass judges the pair,
    this one stays unreported.
    """
    dividend = [round(v * 1.17, 6) for v in ADJACENT_DIVISOR]

    assert _scaled(_adjacent_pair(ADJACENT_DIVISOR, dividend)) == [], (
        "a whole-row rescale between neighbouring rows was reported as reuse"
    )


def test_the_two_precision_predicates_agree_about_non_finite_cells():
    """Neither question means anything for a value that is not a number.

    `_can_pin_a_ratio` divides a decimal step by the value, and for an infinity that
    quotient is 0.0 -- which sails through a tolerance test and claims the cell can
    pin a ratio, while `_is_short_hp` refuses it a run. The two predicates are
    deliberately different questions, but not about this.
    """
    for v in (float("inf"), float("-inf"), float("nan")):
        assert _can_pin_a_ratio(v) is False, f"{v} was said to pin a ratio"
        assert _is_short_hp(v) is False, f"{v} was let into a run"
