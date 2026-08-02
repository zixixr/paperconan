"""Inferring the step a row's numbers were recorded in.

`Sheet` holds floats, so a sheet that wrote `71.20` and a sheet that wrote `71.2`
are indistinguishable by the time a detector sees them -- the trailing zero is gone
at parse. That matters because the ratio arm is being rebuilt around "could this
value have been produced by rounding `k * a` to the step it was recorded in", and
that question needs the step.

The step is therefore inferred from the ROW rather than read off each cell: most
panels write one column of measurements to one precision, so the row's commonest
decimal count is better evidence about any single cell than that cell's own
surviving digits. A cell that kept MORE decimals than its neighbours is left alone,
because that is real precision rather than a lost zero.

It is an inference, not recovered metadata, and it is reported as such
(`precision_source = "row_inferred"`). Getting it wrong narrows a cell's allowed
interval, which loses matches -- but it also hands any surviving match a finer grid
to be scored against, and that direction inflates. The last case here pins how far:
one formula-cached cell can out-resolve its row by eight decades. So this module
establishes an input assumption, not a safety property.

Design: docs/superpowers/specs/2026-07-30-short-row-significance-gate.md section 4.
"""
from __future__ import annotations

import math

from paperconan._audit import _effective_row_quantums


def test_a_trailing_zero_is_recovered_from_the_rest_of_the_row():
    """The headline case. `71.20` reaches us as `71.2` and must not read as coarser.

    Every other value in this row was written to two decimals, so the row's step is
    0.01 and this cell is read at that step rather than at the 0.1 its surviving
    digits suggest.
    """
    q = _effective_row_quantums([71.2, 42.13, 58.91, 33.84, 26.55])

    assert q[0] == 0.01, f"the lost trailing zero was not recovered: {q}"
    assert all(v == 0.01 for v in q), q


def test_a_genuinely_finer_cell_is_not_coarsened_to_the_row():
    """`max`, not the row step alone: extra decimals are real precision.

    Reading this cell at the row's 0.01 would claim it is only known to a hundredth
    when it was written to a millionth, which would widen its allowed interval and
    admit ratios the data does not actually support.
    """
    q = _effective_row_quantums([42.13, 58.91, 19.376543])

    assert q[0] == 0.01 and q[1] == 0.01
    assert math.isclose(q[2], 1e-6, rel_tol=1e-12), q


def test_an_all_integer_row_gets_a_step_of_one():
    """Integers are not excluded outright, they are simply coarse.

    Whether a relation between integer rows is worth reporting is a question for the
    evidence score, not for a precision gate -- a short match on small counts scores
    almost nothing, while a long match across a wide range can still be a signal.
    """
    q = _effective_row_quantums([3.0, 5.0, 8.0, 21.0])

    assert all(v == 1.0 for v in q), q


def test_a_tie_in_the_row_resolves_to_the_finer_step():
    """Two decimal counts equally common: take the finer one.

    The coarser choice would claim cells are known less precisely than they are, and
    a too-wide interval is the direction that invents relations. The finer choice
    only ever narrows, which loses them instead.
    """
    q = _effective_row_quantums([1.5, 2.5, 1.25, 2.25])

    assert all(v == 0.01 for v in q), q


def test_non_finite_cells_get_no_step_and_do_not_set_the_row():
    """NaN and infinities carry no recorded precision and must not vote."""
    q = _effective_row_quantums([float("nan"), 42.13, 58.91, float("inf")])

    assert math.isnan(q[0]) and math.isnan(q[3]), q
    assert q[1] == 0.01 and q[2] == 0.01, q


def test_a_row_with_nothing_finite_yields_no_steps():
    q = _effective_row_quantums([float("nan"), float("inf")])

    assert all(math.isnan(v) for v in q), q


def test_scientific_notation_is_read_at_its_written_precision():
    """A small number written in exponent form still has a decimal step."""
    q = _effective_row_quantums([1.5e-07, 2.5e-07, 3.5e-07])

    assert all(math.isclose(v, 1e-8, rel_tol=1e-12) for v in q), q


def test_the_inference_is_deterministic():
    """Ordering of equally common counts must not depend on dict or set iteration."""
    row = [1.5, 2.5, 1.25, 2.25, 3.5, 4.25]

    assert _effective_row_quantums(row) == _effective_row_quantums(list(row))


def test_one_float_artifact_cell_can_out_resolve_its_whole_row():
    """A KNOWN HAZARD, pinned so a scoring stage cannot inherit it silently.

    `max(cell, row)` protects genuine extra precision, but it has no ceiling. A
    formula-cached value keeps every float decimal it was computed with, so a single
    such cell in an otherwise two-decimal row is read at 1e-10 -- eight decades finer
    than its neighbours, from a spreadsheet that recorded nothing of the sort.

    Matching is unaffected in the dangerous direction: a narrower interval only loses
    runs. Scoring is not. Where evidence is counted as distinguishable positions,
    `range / step`, this cell is worth roughly 38 bits by itself, which clears a
    20-bit reporting gate with no help from any other column.

    Pinned rather than fixed: the ceiling belongs with whichever consumer turns the
    step into a score, and the design requires that consumer to measure its
    sensitivity to this inference. If a fix lands here instead, this test should fail
    and be replaced by one asserting the bound.
    """
    import math

    row = [42.13, 58.91, 1 / 3, 33.84, 26.55]
    q = _effective_row_quantums(row)

    assert q[0] == 0.01 and q[1] == 0.01, q
    assert q[2] <= 1e-9, f"the artifact cell was expected to out-resolve its row: {q}"

    span = 40.0                       # a plausible target spread
    assert math.log2(span / q[2]) > 20, (
        "the hazard is no longer worth pinning here -- re-derive it"
    )
