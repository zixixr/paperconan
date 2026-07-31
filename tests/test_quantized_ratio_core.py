"""Reconstructing a scaled row: which constants k could have produced this row?

The ratio arm is being rebuilt around one question. If row B were produced by taking
row A, multiplying by some constant, and writing the result out at B's recorded
precision, which constants survive? A cell written as `b` at step `q` came from
anything in `[b - q/2, b + q/2]`, so it admits an interval of k rather than a point,
and a run of columns holds together exactly when their intervals intersect.

That replaces a tolerance constant with the arithmetic of rounding itself: nothing
here asks "is the ratio close enough", it asks "could rounding have produced this".

Two properties matter more than any single case and are asserted as such at the end:
every run this returns is genuinely reconstructible, and reading a target cell as
COARSER can never destroy a run that already existed.

Direction matters. Only the target row carries write-back rounding error, because
only it was computed and re-rounded, so A->B and B->A are different questions and
both are asked.

Pure functions, deliberately not wired into any detector -- M1 of
docs/superpowers/specs/2026-07-30-short-row-significance-gate.md sections 5.2-5.3.
All data synthetic.

Signal, not verdict: a reconstructible run is a data inconsistency to ask the
authors about, never an accusation.
"""
from __future__ import annotations

import math

from paperconan._audit import _directed_ratio_interval, _scan_quantized_ratio_runs

# The design's worked example: eight columns at two decimals, B = A * 0.8409 written
# back at the panel's own precision. Invisible to the shipped detector.
SRC = [42.13, 58.91, 19.37, 71.20, 33.84, 26.55, 49.02, 61.78]
TGT = [35.43, 49.54, 16.29, 59.87, 28.46, 22.33, 41.22, 51.95]
Q2 = [0.01] * 8


def _runs(source, target, quantums, **kw):
    return _scan_quantized_ratio_runs(source, target, quantums, **kw)


# --- one column at a time ----------------------------------------------------

def test_a_column_admits_the_constants_that_round_back_to_it():
    lo, hi = _directed_ratio_interval(4.0, 3.0, 0.01)

    assert math.isclose(lo, (3.0 - 0.005) / 4.0)
    assert math.isclose(hi, (3.0 + 0.005) / 4.0)
    assert lo < 0.75 < hi, "the exact ratio must sit inside its own interval"


def test_a_negative_source_keeps_the_interval_the_right_way_round():
    """Dividing by a negative swaps the ends; the interval must still be ordered."""
    lo, hi = _directed_ratio_interval(-4.0, 3.0, 0.01)

    assert lo <= hi, (lo, hi)
    assert lo < -0.75 < hi


def test_a_zero_source_against_a_non_zero_target_breaks_the_run():
    """No constant multiplies zero into something else."""
    assert _directed_ratio_interval(0.0, 3.0, 0.01) is None


def test_zero_against_zero_is_compatible_with_everything_and_confirms_nothing():
    """Any k reproduces 0 from 0, so the column neither breaks nor narrows."""
    lo, hi = _directed_ratio_interval(0.0, 0.0, 0.01)

    assert lo == -math.inf and hi == math.inf


def test_a_non_finite_cell_breaks_the_run():
    for a, b in ((float("nan"), 3.0), (4.0, float("nan")), (float("inf"), 3.0)):
        assert _directed_ratio_interval(a, b, 0.01) is None, (a, b)


# --- runs --------------------------------------------------------------------

def test_the_eight_column_two_decimal_case_reconstructs_as_one_run():
    """The design's motivating example, which the shipped detector reports as nothing.

    The intersection is narrow -- about seven parts in a hundred thousand -- which is
    what makes eight columns agreeing on it worth a second look.
    """
    runs = _runs(SRC, TGT, Q2)

    assert len(runs) == 1, runs
    run = runs[0]
    assert (run["start"], run["end"]) == (0, 7)
    assert run["informative"] == 8
    assert math.isclose(run["k_lo"], 0.840869, abs_tol=1e-6), run
    assert math.isclose(run["k_hi"], 0.840941, abs_tol=1e-6), run
    assert run["k_lo"] < 0.8409 < run["k_hi"]


def test_the_two_directions_are_different_questions():
    """Only the target carries write-back rounding, so swapping them is not symmetric."""
    forward = _runs(SRC, TGT, Q2)
    backward = _runs(TGT, SRC, Q2)

    assert forward and backward
    assert not math.isclose(forward[0]["k_lo"], 1 / backward[0]["k_hi"], rel_tol=1e-9), (
        "the two directions collapsed into one; only the target should be rounded"
    )


def test_a_run_needs_a_column_to_confirm_the_one_that_fixed_k():
    """A single column always 'matches' -- it defines k. Two is the floor for evidence."""
    assert _runs([4.0], [3.0], [0.01]) == []


def test_a_run_needs_two_distinct_source_values():
    """The same source value repeated confirms nothing about a constant."""
    assert _runs([4.0, 4.0, 4.0], [3.0, 3.0, 3.0], [0.01] * 3) == []


def test_a_break_splits_the_scan_into_separate_maximal_runs():
    src = [42.13, 58.91, 0.0, 71.20, 33.84]
    tgt = [35.43, 49.54, 7.77, 59.87, 28.46]

    runs = _runs(src, tgt, [0.01] * 5)

    assert [(r["start"], r["end"]) for r in runs] == [(0, 1), (3, 4)], runs


def test_only_maximal_runs_are_returned():
    """A run contained in a longer one is not a separate finding."""
    runs = _runs(SRC, TGT, Q2)

    assert len(runs) == 1, f"sub-runs of the 8-column run leaked out: {runs}"


def test_a_run_whose_interval_contains_one_is_flagged_not_hidden():
    """Indistinguishable from no scaling at this precision -- the identical arm's case.

    Reported as a property of the run rather than dropped inside the scanner, so the
    caller decides. A scanner that silently swallowed these would make 'no ratio run'
    mean two different things.
    """
    src = [42.13, 58.91, 19.37, 71.20]
    runs = _runs(src, list(src), [0.01] * 4)

    assert len(runs) == 1
    assert runs[0]["contains_one"] is True


def test_a_run_that_cannot_be_unit_conversion_says_so():
    src = [42.13, 58.91, 19.37, 71.20]
    tgt = [round(v * 0.8409, 2) for v in src]

    runs = _runs(src, tgt, [0.01] * 4)

    assert runs[0]["contains_one"] is False
    assert runs[0]["contains_power_of_ten"] is False


def test_a_ten_fold_restatement_is_marked_as_such():
    src = [42.13, 58.91, 19.37, 71.20]
    tgt = [round(v * 10.0, 2) for v in src]

    runs = _runs(src, tgt, [0.01] * 4)

    assert runs[0]["contains_power_of_ten"] is True


# --- the two properties that matter ------------------------------------------

def test_every_returned_run_really_is_reconstructible():
    """The definition, asserted end to end rather than trusted.

    For any k inside a returned interval, every target cell of that run must sit
    within half its own step of `k * source`. If that fails, the scanner is
    reporting runs that rounding could not have produced.
    """
    cases = [
        (SRC, TGT, Q2),
        ([1.5, 2.5, 3.25, 4.75], [3.0, 5.0, 6.5, 9.5], [0.01] * 4),
        ([-4.0, -6.0, -9.0, -12.0], [-1.0, -1.5, -2.25, -3.0], [0.01] * 4),
        ([0.125, 0.5, 0.875, 1.25], [0.25, 1.0, 1.75, 2.5], [0.001] * 4),
    ]
    for src, tgt, qs in cases:
        for run in _runs(src, tgt, qs):
            for k in (run["k_lo"], run["k_hi"], (run["k_lo"] + run["k_hi"]) / 2):
                for i in range(run["start"], run["end"] + 1):
                    if src[i] == 0.0 and tgt[i] == 0.0:
                        continue
                    residual = abs(tgt[i] - k * src[i])
                    assert residual <= qs[i] / 2 + 1e-9, (
                        f"run {run} claims k={k} rebuilds column {i}: "
                        f"|{tgt[i]} - {k}*{src[i]}| = {residual} > {qs[i] / 2}"
                    )


def test_reading_a_target_cell_more_coarsely_cannot_destroy_a_run():
    """Monotonicity. A coarser step widens that column's interval, and a wider
    interval cannot empty an intersection that was already non-empty.

    This is what makes the row-level step inference safe for MATCHING: inferring too
    coarse can only add runs, never remove them. (It is not safe for scoring -- see
    test_row_quantum.py -- which is why the two stages are kept apart.)
    """
    base = _runs(SRC, TGT, Q2)
    assert base

    for widen in (0, 3, 7):
        qs = list(Q2)
        qs[widen] = 0.1
        widened = _runs(SRC, TGT, qs)
        assert widened, f"widening column {widen} destroyed every run"
        covered = any(r["start"] <= base[0]["start"] and r["end"] >= base[0]["end"]
                      for r in widened)
        assert covered, (
            f"widening column {widen} lost the run {base[0]} instead of keeping it"
        )


def test_a_column_that_confirms_nothing_is_not_counted_as_confirmation():
    """Zero against zero is compatible with every k, so it is not evidence.

    Here exactly one column says anything about the constant. Counting the
    zero-against-zero column as a second would turn a run that confirms nothing into
    a reportable one -- and so would lowering the floor to a single informative
    column, since the first column only defines k.

    Distinct sources are satisfied (0 and 4), so nothing else rejects this: the
    informative count is doing the work alone.
    """
    assert _runs([0.0, 4.0], [0.0, 3.0], [0.01, 0.01]) == []


def test_a_power_of_ten_inside_the_interval_counts_even_off_centre():
    """Asked of the interval, not of a chosen k.

    When the target is not an exact multiple of the source, the surviving interval
    straddles 10 without being centred on it. A restatement in different units would
    then read as an arbitrary constant -- the misreading this flag exists to prevent.
    """
    src = [42.137, 58.913, 19.371]
    tgt = [round(v * 10.0, 1) for v in src]

    runs = _runs(src, tgt, [0.1] * 3)

    assert len(runs) == 1, runs
    mid = (runs[0]["k_lo"] + runs[0]["k_hi"]) / 2
    assert mid != 10.0, "pick values whose interval is not centred on the power of ten"
    assert runs[0]["k_lo"] <= 10.0 <= runs[0]["k_hi"]
    assert runs[0]["contains_power_of_ten"] is True
