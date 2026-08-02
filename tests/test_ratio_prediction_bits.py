"""How much does one constant explain? Scoring a reconstructed ratio run.

The reconstruction core says which constants COULD have written a row. It says
nothing about whether that is worth anyone's time: two columns agreeing on a ratio
between two coarse numbers is arithmetic, eight columns agreeing to a part in a
hundred thousand is not.

The score formalises what a person does by eye. One cell estimates k; every later
cell is a prediction that either lands on the recorded value or does not. A cell
recorded at step `q` across a spread `R` had about `R/q` places it could have
landed, so a correct prediction is worth `log2(R/q)` bits. The anchor that fixed k
predicts nothing and scores nothing.

Then the search itself is paid for. Scanning many row pairs and many start columns
is many chances to find something, so `log2(n_tests)` is subtracted -- a score that
ignored how hard it looked would reward looking harder.

This is a model compression score, NOT a probability. It does not say "this is a
one-in-a-million coincidence"; it says "one constant saved about this much
description". Nothing here judges a cause.

Two things this file exists to hold down:

  * the strength ORDER must come out right -- eight coarse columns above three fine
    ones -- because getting that backwards is the defect the whole redesign is for;
  * a single cell must not be able to carry a run over the bar. That is live, not
    hypothetical: `_effective_row_quantums` can read one formula-cached cell eight
    decades finer than its row, so no cell is scored far finer than its own run.

M2 of docs/superpowers/specs/2026-07-30-short-row-significance-gate.md section 6.
Pure function, not wired into any detector. All data synthetic.
"""
from __future__ import annotations

import math

from paperconan._audit import _ratio_prediction_bits

# The design's two comparison cases.
COARSE_LONG = [35.43, 49.54, 16.29, 59.87, 28.46, 22.33, 41.22, 51.95]   # 8 cols, 0.01
FINE_SHORT = [1.099112, 0.989848, 0.990182]                              # 3 cols, 1e-6


def _bits(values, q, n_tests=1000, **kw):
    return _ratio_prediction_bits(values, [q] * len(values), n_tests, **kw)


# --- the ordering the redesign exists to fix ---------------------------------

def test_eight_coarse_columns_outscore_three_fine_ones():
    """The headline inversion, asserted as a fixed comparison.

    The shipped detector reports the three-column case and is silent on the
    eight-column one. Any scoring that keeps that order has not fixed anything.
    """
    long_coarse = _bits(COARSE_LONG, 0.01)
    short_fine = _bits(FINE_SHORT, 1e-6)

    assert long_coarse["bits"] > short_fine["bits"], (long_coarse, short_fine)


def test_the_anchor_column_is_not_paid_for_predicting_itself():
    """One cell fixes k. Only the cells after it are predictions."""
    two = _bits(COARSE_LONG[:2], 0.01)
    three = _bits(COARSE_LONG[:3], 0.01)

    assert two["confirming"] == 1 and three["confirming"] == 2
    assert three["bits"] > two["bits"]


def test_a_column_that_does_not_narrow_k_neither_anchors_nor_confirms():
    """The reconstruction core marks zero-to-zero columns as uninformative.

    A leading such column must be removed before choosing the anchor.  Otherwise the
    first real constraint is incorrectly paid as a confirming prediction.
    """
    masked = _ratio_prediction_bits(
        [0.0, 10.0, 20.0], [0.01, 0.01, 0.01], 1,
        informative=[False, True, True],
    )
    direct = _ratio_prediction_bits([10.0, 20.0], [0.01, 0.01], 1)

    assert masked == direct
    assert masked["confirming"] == 1


def test_a_longer_run_scores_higher_at_the_same_precision():
    scores = [_bits(COARSE_LONG[:n], 0.01)["bits"] for n in range(2, 9)]

    assert scores == sorted(scores), scores


def test_a_finer_step_scores_higher_over_the_same_spread():
    coarse = _bits([10.0, 20.0, 30.0, 40.0], 0.1)
    fine = _bits([10.0, 20.0, 30.0, 40.0], 0.001)

    assert fine["bits"] > coarse["bits"]
    assert math.isclose(fine["bits"] - coarse["bits"], 3 * math.log2(100), abs_tol=1e-6)


# --- paying for the search ---------------------------------------------------

def test_doubling_the_search_costs_exactly_one_bit():
    few = _bits(COARSE_LONG, 0.01, n_tests=1000)
    many = _bits(COARSE_LONG, 0.01, n_tests=2000)

    assert math.isclose(few["bits"] - many["bits"], 1.0, abs_tol=1e-9)


def test_a_single_test_costs_nothing():
    assert _bits(COARSE_LONG, 0.01, n_tests=1)["penalty"] == 0.0
    assert _bits(COARSE_LONG, 0.01, n_tests=0)["penalty"] == 0.0


# --- the two-column boundary, spec section 6.6 -------------------------------

def test_two_columns_one_quantum_apart_are_worth_nothing():
    """Spec 6.6. Two target values that differ by a step have no spread to speak of,
    the range floors at the step itself, and the single confirming cell scores 0.
    """
    for gap in (0, 1):
        got = _bits([10.0, 10.0 + gap * 0.01], 0.01)
        assert got["bits"] <= 0.0, (gap, got)


def test_two_columns_further_apart_start_to_count():
    """The same shape, widening. The progression is the point, not any one value."""
    scores = [_bits([10.0, 10.0 + gap * 0.01], 0.01)["raw_bits"]
              for gap in (0, 1, 2, 10, 100)]

    assert scores == sorted(scores), scores
    assert scores[0] == 0.0
    assert scores[-1] > scores[2] > 0.0


def test_a_two_column_run_at_the_boundary_never_clears_a_twenty_bit_bar():
    """Whatever else changes, these must not become reportable on their own."""
    for gap in (0, 1):
        assert _bits([10.0, 10.0 + gap * 0.01], 0.01)["bits"] < 20.0


# --- one cell must not carry the run -----------------------------------------

def test_no_single_cell_is_scored_far_finer_than_its_own_run():
    """The hazard `_effective_row_quantums` creates, bounded here.

    A formula-cached value in an otherwise two-decimal row is read eight decades
    finer than its neighbours and is worth about 38 bits by itself -- enough to clear
    a 20-bit bar with no support from any other column. No cell is therefore scored
    far finer than the run it belongs to.
    """
    values = [10.0, 20.0, 30.0]
    quantums = [0.01, 1e-10, 0.01]          # one artifact cell

    capped = _ratio_prediction_bits(values, quantums, 1000)
    uncapped = _ratio_prediction_bits(values, quantums, 1000, max_quantum_ratio=0)

    assert uncapped["bits"] - capped["bits"] > 15.0, (capped, uncapped)
    assert max(capped["per_cell"]) < max(uncapped["per_cell"]) - 15.0


def test_the_floor_does_not_touch_an_ordinary_run():
    uniform = _ratio_prediction_bits(COARSE_LONG, [0.01] * 8, 1000)
    unbounded = _ratio_prediction_bits(COARSE_LONG, [0.01] * 8, 1000,
                                       max_quantum_ratio=0)

    assert math.isclose(uniform["bits"], unbounded["bits"], abs_tol=1e-9), (
        "the floor bound a run whose cells all share one step"
    )

    # and a cell only one decade finer than its row is real precision, not artifact
    mixed = _ratio_prediction_bits([10.0, 20.0, 30.0], [0.01, 0.001, 0.01], 1000)
    mixed_unbounded = _ratio_prediction_bits([10.0, 20.0, 30.0], [0.01, 0.001, 0.01],
                                             1000, max_quantum_ratio=0)
    assert math.isclose(mixed["bits"], mixed_unbounded["bits"], abs_tol=1e-9)


# --- housekeeping ------------------------------------------------------------

def test_the_score_is_deterministic():
    a = _bits(COARSE_LONG, 0.01)
    b = _bits(list(COARSE_LONG), 0.01)

    assert a == b


def test_a_run_with_nothing_to_confirm_scores_nothing():
    assert _bits([10.0], 0.01)["bits"] == 0.0
    assert _bits([], 0.01)["bits"] == 0.0


def test_the_range_is_at_least_one_step_wide():
    """`R = max(median step, p90-p10)`, and the floor is not decoration.

    It only shows on a run whose values barely differ AND whose cells do not share
    one step: there, the observed spread is narrower than the step the run is mostly
    recorded at, and without the floor the range collapses below a single step and
    every cell scores zero regardless of how finely it was written.

    Reading it as a floor on the RANGE rather than as a bonus is what makes it
    coherent: the values occupy at least one step, so a cell recorded more finely
    than that really could have distinguished places inside it.
    """
    values = [10.0, 10.0005]
    quantums = [0.01, 0.001]

    floored = _ratio_prediction_bits(values, quantums, 1)
    assert floored["raw_bits"] > 0.0, floored

    uniform = _ratio_prediction_bits(values, [0.01, 0.01], 1)
    assert uniform["raw_bits"] == 0.0, (
        "with one shared step the floor must stay invisible"
    )
