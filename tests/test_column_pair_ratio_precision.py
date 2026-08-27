"""A column pair cannot be asked for a ratio it was never recorded precisely enough to hold.

`detect_relations` judged a constant ratio with `ratio_tol = 1e-9 * |k|`, which is
float-exact equality. Source data stored at a fixed number of SIGNIFICANT FIGURES -- the
ordinary case for exported measurements -- cannot meet that however exact the underlying
relation is: the recovered ratio is scattered by roughly a decade per significant figure
short of full precision, which at ordinary export precision is orders above the gate. A
whole panel whose second group was every value of the first group times one constant read
as nothing at all.

The ratio arm therefore has its own tolerance, `_COLUMN_PAIR_RATIO_RTOL`, flat and
relative. It is flat because the obvious alternative was measured and failed: deriving the
room from each cell's recorded decimals is conservative at fixed decimals and a decade too
tight per trailing zero at fixed significant figures, so it MISSED the same exact scaling
at the largest magnitude tried -- worse, in the regime that matters, than the constant it
was meant to improve on.
"""
from __future__ import annotations

import numpy as np
import pytest

from paperconan._audit import (_COLUMN_PAIR_RATIO_RTOL, _COLUMN_PAIR_RTOL,
                               detect_relations)
from paperconan._sheet import Sheet


def _sig(v, digits=7):
    """Round to `digits` significant figures -- how an exported measurement is stored."""
    return float(f"{v:.{digits}g}")


def _sheet(columns):
    """One numeric block, one column per sequence, with a header row."""
    header = [f"c{i}" for i in range(len(columns))]
    rows = [header]
    for r in range(len(columns[0])):
        rows.append([col[r] for col in columns])
    return Sheet.from_rows(rows), header


def _relations(columns):
    sheet, header = _sheet(columns)
    return list(detect_relations(sheet, 1, sheet.nrows, 0, sheet.ncols, header))


# Invented, irregular, and spread over a decade so the recovered ratio is not accidentally
# exact. NOT taken or adapted from any corpus workbook -- source data never enters git.
_SOURCE = [13.72094, 61.30458, 27.84613, 95.06271, 48.21937]
_K = 1.372641


def _scaled_at_seven_figures():
    return [_sig(v * _K) for v in _SOURCE]


@pytest.mark.parametrize("digits", [7, 8])
def test_a_ratio_held_to_finite_stored_precision_is_reported_once(digits) -> None:
    """The case that motivated this: exact relation, finite stored precision.

    ONCE is the second half, and it needs BOTH rungs. At seven figures the residuals are
    too large for exact_linear to fit at all, so nothing could be double-reported there and
    that rung tests only detection. At eight the fit succeeds and rounding leaves the
    intercept between the two tolerances -- above exact equality, below the width the ratio
    arm was admitted at -- so unless exact_linear's zero-intercept test defers at that same
    width, one relationship is reported twice, as a scaling and again as a line through the
    origin. Neither rung alone covers both halves, and a scaling done in exact binary
    covers neither: it fits with no intercept and looks fine however the guard is written.
    """
    scaled = [_sig(v * _K, digits) for v in _SOURCE]
    found = _relations([_SOURCE, scaled])
    kinds = sorted({f["kind"] for f in found})

    assert kinds == ["constant_ratio"], (
        f"at {digits} stored figures the scaling was reported as {kinds or 'nothing'}")
    assert [f for f in found][0]["ratio"] == pytest.approx(_K, rel=1e-5)


def test_the_old_gate_would_have_missed_it() -> None:
    """Guards the premise, so the test above cannot pass for an unrelated reason.

    If seven significant figures happened to pin the ratio to 1e-9, the detection above
    would say nothing about precision-derived tolerance.
    """
    x = np.asarray(_SOURCE)
    y = np.asarray(_scaled_at_seven_figures())
    ratio = y / x

    assert np.std(ratio) > _COLUMN_PAIR_RTOL * abs(np.mean(ratio)), (
        "fixture ratio is exact to 1e-9; it does not exercise the change")


def test_an_exact_relation_is_still_reported() -> None:
    """Data fine enough for the old gate keeps its verdict."""
    exact = [v * 4.0 for v in _SOURCE]        # powers of two are exact in binary

    found = [f for f in _relations([_SOURCE, exact]) if f["kind"] == "constant_ratio"]

    assert found
    assert found[0]["ratio"] == pytest.approx(4.0)


def test_independent_columns_are_not_reported() -> None:
    other = [58.13049, 22.96718, 70.41285, 36.85502, 84.27166]

    assert not [f for f in _relations([_SOURCE, other]) if f["kind"] == "constant_ratio"]


def test_coarse_columns_are_not_granted_the_room_their_rounding_allows() -> None:
    """Why the tolerance is flat rather than derived from the cells.

    At one decimal and single-digit values the cells' own rounding permits the ratio a
    couple of percent, and at that width almost any pair of columns reads as proportional.
    These two are NOT related by a constant, and the assertion below states where their
    spread sits relative to the cap rather than quoting a figure for it: it is far inside
    what rounding alone would have excused and far outside what the flat tolerance allows.
    """
    coarse = [4.8, 6.2, 5.1, 7.3, 5.9]
    near = [6.0, 7.8, 6.3, 9.2, 7.4]
    spread = np.ptp(np.asarray(near) / np.asarray(coarse))
    assert spread > _COLUMN_PAIR_RATIO_RTOL * 100, "fixture is too close to constant"

    assert not [f for f in _relations([coarse, near]) if f["kind"] == "constant_ratio"]


def test_the_ratio_arm_is_looser_than_exact_equality_but_not_by_much() -> None:
    assert _COLUMN_PAIR_RATIO_RTOL > _COLUMN_PAIR_RTOL
    assert _COLUMN_PAIR_RATIO_RTOL <= 1e-5, (
        "this loose the tolerance stops discriminating; see the sweep in _audit.py")


# A ratio drifting between the cap and a decade above it -- the test below asserts that,
# so the fixture cannot quietly stop straddling. Invented values; no corpus data.
_PROBE_A = [1.03847, 2.61093, 1.47205, 3.08472, 2.19638]
_PROBE_B = [0.8695, 2.18611, 1.23253, 2.58281, 1.83901]


def test_the_tolerance_is_load_bearing_at_its_current_value() -> None:
    """Without this the tolerance is a number no test constrains.

    Every other test here passes with it at 1e-5, so a regression by a decade used to be
    invisible to the whole suite. This pair drifts between the two, which the assertion
    below states rather than quotes. It pins the value from one side; what prices moving it
    is tests/test_column_pair_bench_baseline.py, and neither substitutes for the other.
    """
    ratios = np.asarray(_PROBE_B) / np.asarray(_PROBE_A)
    drift = float(np.max(np.abs(ratios - ratios.mean())) / abs(ratios.mean()))
    assert _COLUMN_PAIR_RATIO_RTOL < drift < 1e-5, (
        f"fixture drift {drift:.2e} no longer straddles the cap; it constrains nothing")

    assert not [f for f in _relations([_PROBE_A, _PROBE_B])
                if f["kind"] == "constant_ratio"]


# One column scaled by a factor sitting in each band the two tolerances cut out. The
# comment beside `abs(mean_ratio - 1) > _COLUMN_PAIR_RATIO_RTOL` argues that a pair can
# otherwise fall BETWEEN the arms -- too far apart for the identity guard, too close for
# the ratio to be worth stating -- and this is that argument as a test rather than as
# prose. Factors are named relative to the two constants, not written out, so the table
# moves with them.
@pytest.mark.parametrize("scale,expected", [
    (1 + _COLUMN_PAIR_RTOL / 2, "identical_column"),
    (1 + _COLUMN_PAIR_RTOL * 30, "exact_linear"),
    (1 + _COLUMN_PAIR_RATIO_RTOL / 2, "exact_linear"),
    (1 + _COLUMN_PAIR_RATIO_RTOL * 3, "constant_ratio"),
    (2.5, "constant_ratio"),
])
def test_every_band_between_the_two_tolerances_has_exactly_one_witness(scale, expected):
    """No gap, across the whole range of scale factors.

    Widening the identity guard without also widening the ratio arm leaves a band with no
    witness at all, which is silent in every other test here. The neighbouring failure --
    one relationship reported twice -- cannot be seen on this fixture, because a scaling
    done in exact binary fits with no intercept; that half is
    test_a_ratio_held_only_to_seven_figures_is_reported_once.
    """
    found = sorted({f["kind"] for f in _relations([_SOURCE, [v * scale for v in _SOURCE]])})

    assert found == [expected], f"scale {scale!r} reported {found or 'nothing'}"
