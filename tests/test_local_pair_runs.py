"""Local pair runs: two lines of one block related as B = k*A or B = A + c over part of the
line only (docs/superpowers/specs/2026-10-15-local-pair-runs-design.md). Synthetic data only."""
import math
import random

import numpy as np
import pytest

from paperconan._sheet import Sheet
from paperconan._audit import (
    _LOCAL_RUN_DEPARTURE_CAP, _LOCAL_RUN_MAX_EXAMPLE_POSITIONS, _best_local_group, _edge_steps,
    _effective_line_quantums, _effective_row_quantums, _group_constant_runs, _identity_steps,
    _local_group_bits, _local_line_usable, _outside_departure, _ratio_prediction_bits,
    _scan_interval_runs, _segments, _stretch_text,
    _symmetric_offset_interval, _symmetric_ratio_interval, _zero_source_miss,
    detect_local_pair_runs,
)

A10 = [12.437, 8.912, 15.226, 9.871, 11.054, 13.388, 7.645, 10.902, 14.117, 9.333]


def _arr(xs):
    return np.array(xs, dtype=float)


# --- steps --------------------------------------------------------------------------------

def test_fixed_decimals_line_reads_like_the_row_inference():
    line = [1.25, 3.5, 2.75, 4.0, 0.0, math.nan]
    got, want = _effective_line_quantums(line), _effective_row_quantums(line)
    assert [g if not math.isnan(g) else "nan" for g in got] == [w if not math.isnan(w) else "nan" for w in want]


def test_fixed_significant_figures_line_reads_each_cell_at_its_magnitude():
    line = [123.4567, 234.5678, 45.67891, 56.78912, 67.89123]
    assert _effective_line_quantums(line) == pytest.approx([1e-4, 1e-4, 1e-5, 1e-5, 1e-5])
    # the row inference reads the three-digit cells ten times too finely
    assert _effective_row_quantums(line)[0] == pytest.approx(1e-5)


def test_a_cell_that_lost_trailing_zeros_keeps_the_lines_step():
    line = [123.4, 234.5678, 45.67891, 56.78912, 67.89123]   # 123.4 was 123.4000
    assert _effective_line_quantums(line)[0] == pytest.approx(1e-4)


def test_a_zero_reads_at_the_lines_step_and_non_finite_cells_are_nan():
    q = _effective_line_quantums([0.0, 234.5678, 45.67891, 56.78912, 67.89123, math.nan])
    assert q[0] == pytest.approx(1e-5)
    assert math.isnan(q[-1])


def test_a_cell_with_more_digits_than_the_mode_keeps_them():
    q = _effective_line_quantums([123.4567, 234.5678, 45.67891, 56.78912, 67.89123, 1 / 3])
    assert q[-1] == pytest.approx(1e-10)


def test_a_tie_keeps_fixed_decimals_where_the_readings_differ():
    assert _effective_line_quantums([12.34, 56.78, 1.234, 4.567, 8.91, 2.34]) == \
        pytest.approx([1e-2, 1e-2, 1e-3, 1e-3, 1e-2, 1e-2])


def test_whole_numbers_do_not_vote_for_significant_figures():
    assert _effective_line_quantums([50.0, 25.0, 12.5, 6.3, 3.1, 1.6]) == pytest.approx([0.1] * 6)


def test_zeros_do_not_vote():
    assert _effective_line_quantums([0, 0, 0, 0, 123.4, 234.5678, 45.67891])[4] == pytest.approx(1e-4)


def test_equally_common_figure_counts_take_the_finer_reading():
    q = _effective_line_quantums([123.4567, 45.67891, 6.789123, 12.3456, 1.23456, 345.678])
    assert q[3] == pytest.approx(1e-5)


def test_a_mostly_zero_line_reads_its_zeros_at_its_finest_step():
    q = _effective_line_quantums([0, 0, 0, 0, 0, 0.001234, 0.01234, 0.002345, 0.1234])
    assert q[:5] == pytest.approx([1e-6] * 5)
    q = _effective_line_quantums([0, 0, 0, 0, 1.23, 4.56, 7.89])          # fixed decimals
    assert q == pytest.approx([0.01] * 7)


def test_a_value_too_small_to_read_takes_the_lines_finest_step():
    assert _effective_line_quantums([0.0123, 0.000234, 3.45e-5, 1.2e-12])[3] == pytest.approx(1e-7)


def test_the_magnitude_is_taken_at_the_read_precision():
    q = _effective_line_quantums([123.4567, 234.5678, 45.67891, 56.78912, 99.99999999999])
    assert q[-1] == pytest.approx(1e-4)


def test_a_line_with_no_non_zero_cell_keeps_the_row_step():
    assert _effective_line_quantums([0.0, 0.0, math.nan])[:2] == [1.0, 1.0]


def test_a_blank_cell_does_not_set_a_zeros_step():
    assert _effective_line_quantums([math.nan, 0.0, 234.5678, 45.67891, 56.78912])[1] == pytest.approx(1e-5)


# --- intervals ----------------------------------------------------------------------------

def test_symmetric_ratio_interval_spans_both_roundings():
    lo, hi = _symmetric_ratio_interval(2.0, 3.0, 0.1, 0.1)
    assert lo == pytest.approx(2.95 / 2.05) and hi == pytest.approx(3.05 / 1.95)


def test_symmetric_ratio_interval_with_a_negative_source_is_ordered():
    lo, hi = _symmetric_ratio_interval(-2.0, 3.0, 0.1, 0.1)
    assert lo <= hi < 0


def test_zero_source_confirms_nothing_against_zero_and_breaks_a_run_otherwise():
    assert _symmetric_ratio_interval(0.0, 0.0, 0.1, 0.1) == (-math.inf, math.inf)
    assert _symmetric_ratio_interval(0.0, 1.0, 0.1, 0.1) is None
    assert _symmetric_ratio_interval(math.nan, 1.0, 0.1, 0.1) is None
    assert _symmetric_ratio_interval(2.0, 3.0, math.nan, 0.1) is None


def test_symmetric_ratio_interval_sign_cases():
    assert _symmetric_ratio_interval(-2.0, 3.0, 0.1, 0.1) == pytest.approx((3.05 / -1.95, 2.95 / -2.05))
    assert _symmetric_ratio_interval(-2.0, -3.0, 0.1, 0.1) == pytest.approx((2.95 / 2.05, 3.05 / 1.95))
    assert _symmetric_ratio_interval(5.0, 0.0, 0.1, 0.1) == pytest.approx((-0.05 / 4.95, 0.05 / 4.95))


def test_symmetric_ratio_interval_matches_a_brute_force_oracle():
    rng = random.Random(20261016)
    for _ in range(300):
        a = rng.choice((-1.0, 1.0)) * rng.uniform(0.2, 9.0)
        b = rng.uniform(-9.0, 9.0)
        qa, qb = rng.choice((0.1, 0.01)), rng.choice((0.1, 0.01))
        lo, hi = _symmetric_ratio_interval(a, b, qa, qb)
        grid = [(b + qb * (j / 20 - 0.5)) / (a + qa * (i / 20 - 0.5)) for i in range(21) for j in range(21)]
        assert lo == pytest.approx(min(grid), rel=1e-12) and hi == pytest.approx(max(grid), rel=1e-12)


def test_a_source_read_as_zero_is_written_only_by_a_large_enough_constant():
    assert _zero_source_miss(0.1, 0.1, 0.1, 2.0) == pytest.approx(0.0)
    assert _zero_source_miss(0.3, 0.1, 0.1, 2.0) == pytest.approx(0.25 - 0.1)
    assert _zero_source_miss(-0.2, 0.1, 0.1, 3.0) == pytest.approx(0.0)
    assert _zero_source_miss(0.04, 0.1, 0.1, 0.0) == pytest.approx(0.0)      # target box holds 0
    assert _zero_source_miss(0.3, 0.1, 0.1, -2.0) == pytest.approx(0.15)     # either sign of k
    for args in ((math.nan, 0.1, 0.1, 2.0), (0.3, math.nan, 0.1, 2.0), (0.3, 0.1, math.nan, 2.0), (0.3, 0.1, 0.1, math.nan)):
        assert _zero_source_miss(*args) == math.inf


def test_symmetric_offset_interval():
    lo, hi = _symmetric_offset_interval(2.0, 5.0, 0.1, 0.01)
    assert lo == pytest.approx(3.0 - 0.055) and hi == pytest.approx(3.0 + 0.055)
    assert _symmetric_offset_interval(2.0, math.nan, 0.1, 0.1) is None


# --- runs and groups ----------------------------------------------------------------------

def test_scan_interval_runs_breaks_on_none_and_drops_sub_runs():
    spans = [(1.0, 2.0), (1.5, 3.0), None, (4.0, 5.0), (4.5, 6.0), (4.8, 4.9)]
    runs = _scan_interval_runs(spans, [1, 2, 3, 4, 5, 6], min_distinct_source=2)
    assert [(r["start"], r["end"]) for r in runs] == [(0, 1), (3, 5)]
    assert runs[1]["lo"] == pytest.approx(4.8) and runs[1]["hi"] == pytest.approx(4.9)


def test_an_unbounded_span_joins_a_run_without_informing_it():
    spans = [(1.0, 2.0), (-math.inf, math.inf), (1.5, 2.5)]
    (run,) = _scan_interval_runs(spans, [1, 2, 3], min_distinct_source=2)
    assert (run["start"], run["end"]) == (0, 2)
    assert _scan_interval_runs(spans, [1, 2, 3], min_informative=3, min_distinct_source=2) == []


def test_intervals_that_only_touch_share_a_point():
    (run,) = _scan_interval_runs([(1.0, 2.0), (2.0, 3.0), (2.0, 2.5)], [1, 2, 3])
    assert (run["start"], run["end"], run["lo"], run["hi"]) == (0, 2, 2.0, 2.0)


def test_a_run_needs_distinct_source_values_among_its_informative_cells():
    spans = [(1.9, 2.1)] * 4
    assert _scan_interval_runs(spans, [5.0, 5.0, 6.0, 6.0]) == []
    assert len(_scan_interval_runs(spans, [5.0, 5.0, 6.0, 7.0])) == 1
    assert _scan_interval_runs([(1.9, 2.1), (-math.inf, math.inf), (1.9, 2.1), (1.9, 2.1)], [5.0, 7.0, 6.0, 6.0]) == []


def test_a_run_starting_inside_another_can_reach_further():
    spans = [(1.0, 2.0), (1.5, 2.5), (2.2, 3.0), (2.6, 3.5)]
    runs = _scan_interval_runs(spans, [1, 2, 3, 4], min_distinct_source=2)
    assert [(r["start"], r["end"], r["lo"], r["hi"]) for r in runs] == [
        (0, 1, 1.5, 2.0), (1, 2, 2.2, 2.5), (2, 3, 2.6, 3.0)]


def test_runs_of_one_constant_form_one_group_with_the_lone_cells_between():
    spans = [(1.9, 2.1)] * 3 + [(5.0, 6.0), (1.95, 2.05), (7.0, 8.0)] + [(1.9, 2.1)] * 3
    runs = _scan_interval_runs(spans, list(range(9)))
    (group, *others) = _group_constant_runs(runs, spans)
    assert others == []
    assert group["positions"] == [0, 1, 2, 4, 6, 7, 8]
    assert group["segments"] == [[0, 2], [4, 4], [6, 8]]
    assert _segments([0, 1, 3]) == [[0, 1], [3, 3]]


def test_a_cell_without_an_interval_joins_a_group_only_when_admitted():
    spans = [(1.9, 2.1)] * 3 + [None] + [(1.9, 2.1)] * 2
    runs = _scan_interval_runs(spans, list(range(6)), min_distinct_source=2)
    (group,) = _group_constant_runs(runs, spans)
    assert group["positions"] == [0, 1, 2, 4, 5]
    (group,) = _group_constant_runs(runs, spans, admit=lambda i, lo, hi: i == 3 and lo <= 2.0 <= hi)
    assert group["positions"] == [0, 1, 2, 3, 4, 5] and group["segments"] == [[0, 5]]
    assert 3 not in group["informative"]


def test_a_run_joins_the_longest_group_its_constant_still_meets():
    sep = (9.0, 9.5)
    spans = [(2.3, 2.5)] * 4 + [sep] + [(2.05, 2.35)] * 3 + [sep] + [(1.9, 2.1)] * 6
    first, second = _group_constant_runs(_scan_interval_runs(spans, list(range(len(spans)))), spans)
    assert (first["lo"], first["hi"]) == (2.05, 2.1) and first["positions"] == [5, 6, 7, 9, 10, 11, 12, 13, 14]
    assert (second["lo"], second["hi"]) == (2.3, 2.5) and second["positions"] == [0, 1, 2, 3, 5, 6, 7]


def test_a_cell_that_confirms_nothing_sits_inside_a_stretch_but_never_extends_one():
    every = (-math.inf, math.inf)
    spans = [every] * 4 + [(1.9, 2.1), (1.95, 2.05), every, (1.92, 2.08), every, every, (5.0, 6.0)]
    (group,) = _group_constant_runs(_scan_interval_runs(spans, list(range(len(spans)))), spans)
    assert group["positions"] == [4, 5, 6, 7] and group["segments"] == [[4, 7]]
    assert group["informative"] == [4, 5, 7]


# --- outside departure ---------------------------------------------------------------------

def test_outside_departure_measures_a_source_read_as_zero_by_its_miss():
    a, b = _arr([1.0, 2.0, 3.0, 0.0]), _arr([2.0, 4.0, 6.0, 5.0])
    q = [0.1] * 4
    spans = [_symmetric_ratio_interval(x, y, 0.1, 0.1) for x, y in zip(a, b)]
    group = {"lo": 1.99, "hi": 2.01, "positions": [0, 1, 2]}
    # k = 2 writes 0 +/- 0.05 as at most 0.1; B's box starts at 4.95: a miss of 4.85 over median |b| 4.5
    assert _outside_departure(a, b, q, q, group, spans, "ratio") == pytest.approx(4.85 / 4.5)


def test_outside_departure_is_none_for_a_whole_line_relation():
    a, b = _arr([1.0, 2.0, 3.0]), _arr([2.0, 4.0, 6.0])
    spans = [_symmetric_ratio_interval(x, y, 0.1, 0.1) for x, y in zip(a, b)]
    q = [0.1] * 3
    assert _outside_departure(a, b, q, q, {"lo": 1.9, "hi": 2.1, "positions": [0, 1, 2]}, spans, "ratio") is None


def test_outside_departure_of_an_offset_is_its_gap_over_the_constant():
    a, b = _arr([1.0, 2.0, 3.0, 4.0, 5.0]), _arr([4.0, 5.0, 6.0, 7.5, 7.6])
    q = [0.1] * 5
    spans = [_symmetric_offset_interval(x, y, 0.1, 0.1) for x, y in zip(a, b)]
    group = {"lo": 2.9, "hi": 3.1, "positions": [0, 1, 2]}
    # c = 3 against [3.4, 3.6] below it and [2.5, 2.7] above it: gaps 0.4 and 0.3 over |c|, their median
    assert _outside_departure(a, b, q, q, group, spans, "offset") == pytest.approx((0.4 + 0.3) / 2 / 3.0)


def test_outside_departure_of_a_ratio_is_read_against_the_lines_scale():
    a, b = _arr([10.0, 20.0, 30.0, 0.5]), _arr([20.0, 40.0, 60.0, 1.3])
    q = [0.1] * 4
    spans = [_symmetric_ratio_interval(x, y, 0.1, 0.1) for x, y in zip(a, b)]
    group = {"lo": 1.99, "hi": 2.01, "positions": [0, 1, 2]}
    # the small cell admits k from 1.25/0.55; that gap as an amount of B, over the line's median |b|
    # of 30 -- not over the cell's own 1.3, which would read a small additive error as a large one
    assert _outside_departure(a, b, q, q, group, spans, "ratio") == pytest.approx((1.25 / 0.55 - 2.0) * 0.5 / 30.0)


def test_outside_departure_reads_its_scale_from_cells_that_confirm_something():
    a, b = _arr([10.0, 20.0, 30.0, 0.5] + [0.0] * 4), _arr([20.0, 40.0, 60.0, 1.3] + [0.0] * 4)
    q = [0.1] * 8
    spans = [_symmetric_ratio_interval(x, y, 0.1, 0.1) for x, y in zip(a, b)]
    group = {"lo": 1.99, "hi": 2.01, "positions": [0, 1, 2]}
    # four zeros against zeros: they neither set the median |b|, still 30, nor count as departures
    assert _outside_departure(a, b, q, q, group, spans, "ratio") == pytest.approx((1.25 / 0.55 - 2.0) * 0.5 / 30.0)


def test_outside_departure_reads_its_scale_over_the_pair_only():
    # B against a blank in A is not part of the pair: it does not pull the median |b| to 0
    a = _arr([10.0, 20.0, 30.0, 0.0] + [math.nan] * 5)
    b = _arr([20.0, 40.0, 60.0, 5.0] + [0.0] * 5)
    q_a, q_b = [0.1] * 4 + [math.nan] * 5, [0.1] * 9
    spans = [_symmetric_ratio_interval(x, y, s, t) for x, y, s, t in zip(a, b, q_a, q_b)]
    group = {"lo": 1.99, "hi": 2.01, "positions": [0, 1, 2]}
    # the source read as zero misses 5 by 4.95 - 2 * 0.05; the pair's median |b| is that of 20, 40, 60, 5
    assert _outside_departure(a, b, q_a, q_b, group, spans, "ratio") == pytest.approx(4.85 / 30.0)


def test_outside_departure_is_the_median_of_the_outside_not_its_mean():
    a, b = _arr([1.0, 2.0, 3.0, 4.0, 5.0, 6.0]), _arr([4.0, 5.0, 6.0, 7.5, 7.6, 20.0])
    q = [0.1] * 6
    spans = [_symmetric_offset_interval(x, y, 0.1, 0.1) for x, y in zip(a, b)]
    group = {"lo": 2.9, "hi": 3.1, "positions": [0, 1, 2]}
    # gaps 0.4, 0.3 and 10.9 over |c|: the median is the first, the mean far above it
    assert _outside_departure(a, b, q, q, group, spans, "offset") == pytest.approx(0.4 / 3.0)


def test_outside_departure_is_capped_when_the_lines_scale_is_zero():
    a = _arr([10.0, 20.0, 30.0, 11.0, 12.0, 13.0, 14.0])
    b = _arr([20.0, 40.0, 60.0, 0.0, 0.0, 0.0, 0.0])
    q = [0.1] * 7
    spans = [_symmetric_ratio_interval(x, y, 0.1, 0.1) for x, y in zip(a, b)]
    group = {"lo": 1.99, "hi": 2.01, "positions": [0, 1, 2]}
    assert _outside_departure(a, b, q, q, group, spans, "ratio") == _LOCAL_RUN_DEPARTURE_CAP


def test_a_source_read_as_zero_departs_at_the_cap_when_the_pairs_scale_is_zero():
    # inside the stretch k * a falls below half B's step more often than not: B reads 0 there,
    # those cells are part of the pair, and the pair's median |b| is 0
    a = _arr([20.0, 40.0, 60.0, 0.5, 0.6, 0.7, 0.8, 0.9, 0.0])
    b = _arr([1.0, 2.0, 3.0, 0.0, 0.0, 0.0, 0.0, 0.0, 5.0])
    q = [0.1] * 9
    spans = [_symmetric_ratio_interval(x, y, 0.1, 0.1) for x, y in zip(a, b)]
    assert all(s is not None and s[0] <= 0.05 <= s[1] for s in spans[:8])     # k = 0.05 writes every inside cell
    group = {"lo": 0.049, "hi": 0.051, "positions": list(range(8))}
    assert _outside_departure(a, b, q, q, group, spans, "ratio") == _LOCAL_RUN_DEPARTURE_CAP


def test_outside_departure_refuses_a_constant_of_zero():
    a, b = _arr([10.0, 20.0, 30.0, 5.0]), _arr([0.0, 0.0, 0.0, 13.0])
    q = [0.1] * 4
    spans = [_symmetric_ratio_interval(x, y, 0.1, 0.1) for x, y in zip(a, b)]
    group = {"lo": -0.001, "hi": 0.001, "positions": [0, 1, 2]}
    assert _outside_departure(a, b, q, q, group, spans, "ratio") is None


@pytest.mark.parametrize("relation", ["ratio", "offset"])
@pytest.mark.parametrize("blank", ["a", "b"])
def test_a_cell_blank_on_one_side_is_neither_a_departure_nor_an_edge(relation, blank):
    a = [10.0, 20.0, 30.0, 40.0, 5.0]
    b = [20.0, 40.0, 60.0, 80.0, 13.0] if relation == "ratio" else [20.0, 30.0, 40.0, 50.0, 13.0]
    (a if blank == "a" else b)[3] = math.nan
    a, b = _arr(a), _arr(b)
    q_a = [0.1 if math.isfinite(x) else math.nan for x in a]
    q_b = [0.1 if math.isfinite(x) else math.nan for x in b]
    interval = _symmetric_ratio_interval if relation == "ratio" else _symmetric_offset_interval
    spans = [interval(x, y, s, t) for x, y, s, t in zip(a, b, q_a, q_b)]
    lo, hi = (1.99, 2.01) if relation == "ratio" else (9.9, 10.1)
    group = {"lo": lo, "hi": hi, "positions": [0, 1, 2], "segments": [[0, 2]]}
    # the edge is read past the half-blank cell, at (5, 13)
    want = (12.95 / 5.05 - 2.0) * 5.0 / 0.1 if relation == "ratio" else (10.0 - 8.1) / 0.1
    assert _edge_steps(a, b, q_a, q_b, group, spans, relation) == pytest.approx([want])
    # with (5, 13) in the group, the half-blank cell is all that lies outside: nothing to measure
    group = {"lo": lo, "hi": hi, "positions": [0, 1, 2, 4], "segments": [[0, 2], [4, 4]]}
    assert _outside_departure(a, b, q_a, q_b, group, spans, relation) is None


@pytest.mark.filterwarnings("error")
def test_identity_steps_with_no_informative_cell_refuse_nothing():
    assert _identity_steps([(-math.inf, math.inf)] * 3, [], 2.0, "ratio") == math.inf


def test_identity_steps_are_read_in_the_groups_half_widths():
    spans = [(1.9, 2.1), (1.8, 2.2), (1.95, 2.05), (-math.inf, math.inf)]
    # half-widths 0.1, 0.2 and 0.05 over the informative cells: a median of 0.1
    assert _identity_steps(spans, [0, 1, 2], 2.0, "ratio") == pytest.approx(10.0)
    assert _identity_steps(spans, [0, 1, 2], 0.3, "offset") == pytest.approx(3.0)        # counted from 0, not 1


def test_edge_steps_count_a_ratio_gap_in_the_targets_steps():
    a, b = _arr([10.0, 20.0, 30.0, 5.0]), _arr([20.0, 40.0, 60.0, 13.0])
    q = [0.1] * 4
    spans = [_symmetric_ratio_interval(x, y, 0.1, 0.1) for x, y in zip(a, b)]
    group = {"lo": 1.99, "hi": 2.01, "positions": [0, 1, 2], "segments": [[0, 2]]}
    # nothing precedes the stretch; past its end the cell admits k from 12.95/5.05, a gap that is
    # (12.95/5.05 - 2) * 5 as an amount of B, counted in B's steps
    assert _edge_steps(a, b, q, q, group, spans, "ratio") == pytest.approx([(12.95 / 5.05 - 2.0) * 5.0 / 0.1])


def test_edge_steps_walk_past_a_blank_and_the_groups_own_positions():
    a = _arr([10.0, 20.0, 30.0, math.nan, 15.0, 5.0])
    b = _arr([20.0, 40.0, 60.0, math.nan, 30.0, 13.0])
    q = [0.1] * 6
    spans = [_symmetric_ratio_interval(x, y, 0.1, 0.1) for x, y in zip(a, b)]
    group = {"lo": 1.99, "hi": 2.01, "positions": [0, 1, 2, 4], "segments": [[0, 2], [4, 4]]}
    assert _edge_steps(a, b, q, q, group, spans, "ratio") == pytest.approx([(12.95 / 5.05 - 2.0) * 5.0 / 0.1])


def test_edge_steps_count_an_offset_gap_in_the_pairs_combined_step():
    a, b = _arr([1.0, 2.0, 3.0, 4.0]), _arr([4.0, 5.0, 6.0, 7.5])
    q_a, q_b = [0.1] * 4, [0.01] * 4
    spans = [_symmetric_offset_interval(x, y, s, t) for x, y, s, t in zip(a, b, q_a, q_b)]
    group = {"lo": 2.9, "hi": 3.1, "positions": [0, 1, 2], "segments": [[0, 2]]}
    # the last cell admits c in 3.5 +/- 0.055: a gap of 0.445, in steps of (0.1 + 0.01) / 2
    assert _edge_steps(a, b, q_a, q_b, group, spans, "offset") == pytest.approx([0.445 / 0.055])


def test_edge_steps_walk_past_a_cell_that_confirms_nothing():
    a, b = _arr([10.0, 20.0, 30.0, 0.0, 5.0]), _arr([20.0, 40.0, 60.0, 0.0, 13.0])
    q = [0.1] * 5
    spans = [_symmetric_ratio_interval(x, y, 0.1, 0.1) for x, y in zip(a, b)]
    group = {"lo": 1.99, "hi": 2.01, "positions": [0, 1, 2], "segments": [[0, 2]]}
    assert _edge_steps(a, b, q, q, group, spans, "ratio") == pytest.approx([(12.95 / 5.05 - 2.0) * 5.0 / 0.1])


def test_edge_steps_read_the_segment_with_the_most_informative_cells():
    a = _arr([10.0, 0.0, 0.0, 0.0, 20.0, 30.0, 5.0, 7.0, 11.0, 12.0, 13.0, 14.0, 9.0])
    b = _arr([20.0, 0.0, 0.0, 0.0, 40.0, 60.0, 13.0, 30.0, 22.0, 24.0, 26.0, 28.0, 40.0])
    q = [0.1] * 13
    spans = [_symmetric_ratio_interval(x, y, 0.1, 0.1) for x, y in zip(a, b)]
    group = {"lo": 1.99, "hi": 2.01, "positions": [0, 1, 2, 3, 4, 5, 8, 9, 10, 11],
             "segments": [[0, 5], [8, 11]]}
    # [0, 5] spans six positions but informs at three; [8, 11] informs at four: its edges are judged
    assert _edge_steps(a, b, q, q, group, spans, "ratio") == pytest.approx(
        [(29.95 / 7.05 - 2.0) * 7.0 / 0.1, (39.95 / 9.05 - 2.0) * 9.0 / 0.1])


def test_edge_steps_take_the_earlier_of_two_equally_long_segments():
    a = _arr([10.0, 20.0, 30.0, 5.0, 8.0, 7.0, 11.0, 12.0, 13.0, 9.0])
    b = _arr([20.0, 40.0, 60.0, 13.0, 3.0, 30.0, 22.0, 24.0, 26.0, 40.0])
    q = [0.1] * 10
    spans = [_symmetric_ratio_interval(x, y, 0.1, 0.1) for x, y in zip(a, b)]
    group = {"lo": 1.99, "hi": 2.01, "positions": [0, 1, 2, 6, 7, 8], "segments": [[0, 2], [6, 8]]}
    assert _edge_steps(a, b, q, q, group, spans, "ratio") == pytest.approx([(12.95 / 5.05 - 2.0) * 5.0 / 0.1])


def test_edge_steps_count_a_zero_source_in_the_targets_steps():
    a, b = _arr([3.42, 1.87, 2.95, 0.0]), _arr([8.6, 4.7, 7.4, 0.3])
    q_a, q_b = [0.01] * 4, [0.1] * 4
    spans = [_symmetric_ratio_interval(x, y, s, t) for x, y, s, t in zip(a, b, q_a, q_b)]
    group = {"lo": 2.49, "hi": 2.51, "positions": [0, 1, 2], "segments": [[0, 2]]}
    # B's box starts at 0.25; k = 2.5 writes 0 +/- 0.005 as at most 0.0125: a miss of 0.2375, in B's steps
    assert _edge_steps(a, b, q_a, q_b, group, spans, "ratio") == pytest.approx([0.2375 / 0.1])


def test_a_line_is_usable_with_enough_cells_and_no_axis_shape():
    assert _local_line_usable(_arr([1.2, 3.4, math.nan, 2.7, 5.1]))
    assert not _local_line_usable(_arr([1.2, 3.4, math.nan, 2.7]))           # three finite cells
    assert not _local_line_usable(_arr([1.2, 3.4, 2.7, 5.1]), "Time (h)")      # an axis by its label
    assert not _local_line_usable(_arr([0.5, 1.0, math.nan, 1.5, 2.0]))       # a dose or time grid


def test_a_line_that_is_itself_a_grid_within_rounding_is_not_compared():
    assert not _local_line_usable(_arr([0.1, 0.2, 0.3, 0.4, 0.5]))                         # float error in a typed grid
    assert not _local_line_usable(_arr([round(i / 3, 2) for i in range(1, 9)]))           # thirds, at 2 dp
    assert not _local_line_usable(_arr([0.0, 1.0, 2.0, 3.0]))                              # a time axis from 0
    assert not _local_line_usable(_arr([100 / 2 ** i for i in range(8)] + [0.0]))          # a dilution and its vehicle
    assert not _local_line_usable(_arr([float(f"{50 / 3 ** i:.3g}") for i in range(7)]))   # 3-fold, at 3 figures
    assert not _local_line_usable(_arr([4.2] * 6))                                        # a constant line
    ladder = [100 / 2 ** i for i in range(6)]
    assert not _local_line_usable(_arr([x for x in ladder for _ in range(3)]))            # each dose, once per replicate
    assert not _local_line_usable(_arr(ladder + [0.0] + ladder + [0.0]))                  # the ladder listed twice
    assert _local_line_usable(_arr([0.0, 0.0, 3.2, 6.4, 0.0]))                            # two ratios are not a ladder
    assert _local_line_usable(_arr([10.01, 11.02, 12.08, 13.01, 14.02]))                  # off any grid by more than rounding


# --- the decision -------------------------------------------------------------------------

def _best(a, b, n_tests=4):
    a, b = _arr(a), _arr(b)
    return _best_local_group(a, b, _effective_line_quantums(a), _effective_line_quantums(b), n_tests)


def test_a_stretch_of_a_scaled_copy_is_reported():
    b = [round(x * 1.137, 3) for x in A10[:6]] + [11.9, 9.1, 18.7, 6.2]
    relation, group, bits, departure = _best(A10, b)
    assert relation == "ratio" and group["positions"] == [0, 1, 2, 3, 4, 5]
    assert (group["lo"] + group["hi"]) / 2 == pytest.approx(1.137, rel=1e-4)
    assert departure > 0.05


def test_a_relation_over_the_whole_line_is_left_to_the_whole_line_detectors():
    assert _best(A10, [round(x * 1.137, 3) for x in A10]) is None


def test_a_stretch_of_a_shifted_copy_is_reported_at_one_decimal():
    w = [21.3, 22.8, 20.9, 23.4, 22.1, 21.7, 24.0, 22.6, 20.5, 23.1]
    shifted = [round(x + 3.0, 1) for x in w[:8]] + [22.0, 25.9]
    relation, group, _bits, _dep = _best(w, shifted)
    assert relation == "offset" and group["positions"] == list(range(8))


def test_two_stretches_of_one_constant_are_one_finding():
    a = A10 + [10.051, 12.884]
    b = ([round(x * 0.6, 3) for x in a[:5]] + [7.5, 4.0, 9.3, 7.1]
         + [round(x * 0.6, 3) for x in a[9:]])
    relation, group, _bits, _dep = _best(a, b)
    assert relation == "ratio" and group["segments"] == [[0, 4], [9, 11]]


def test_near_proportional_curve_rows_are_not_a_stretch():
    resp = [10 / (1 + math.exp(-(-3 + 6 * j / 7))) for j in range(8)]
    assert _best([round(1.2 * v, 2) for v in resp], [round(1.5 * v, 2) for v in resp], 400) is None


@pytest.mark.parametrize("k", [10.0, 1.0])
def test_a_unit_restatement_or_an_identity_is_not_reported(k):
    b = [round(x * k, 3) for x in A10[:6]] + [11.9, 9.1, 18.7, 6.2]
    assert _best(A10, b) is None


def test_a_short_coarse_stretch_does_not_carry_enough_information():
    a = [2.1, 3.4, 5.2, 4.4, 6.1, 3.9]
    assert _best(a, [round(x * 2.5, 2) for x in a[:3]] + [8.0, 9.3, 11.1]) is None


def test_consistency_scattered_along_the_line_is_not_a_stretch():
    a = [12.437, 8.912, 15.226, 9.871, 11.054, 13.388, 7.645, 10.902, 14.117, 9.333,
         10.051, 12.884, 9.4466, 13.1027, 11.7739]
    b = [round(x * 1.137, 3) for x in a]
    for i, v in ((3, 2.5), (7, 30.0), (11, 4.2)):           # three breaks, stretches of three
        b[i] = v
    assert _best(a, b) is None


def test_a_constant_a_few_rounding_steps_from_the_identity_is_not_reported():
    w = [21.3, 22.8, 20.9, 23.4, 22.1, 21.7, 24.0, 22.6, 20.5, 23.1]
    shifted = [round(x + 0.3, 1) for x in w[:8]] + [22.0, 25.9]
    assert _best(w, shifted) is None


def test_a_plateau_with_gradual_edges_is_not_reported():
    a = [10.0, 11.3, 12.5, 13.6, 14.9, 16.2, 17.4, 18.7, 19.9, 21.2, 22.6, 23.8]
    drift = [1.0, 1.1, 1.2, 1.3, 1.4, 1.4, 1.4, 1.4, 1.4, 1.4, 1.5, 1.6]
    assert _best(a, [round(x + d, 1) for x, d in zip(a, drift)]) is None


def test_the_edge_is_read_past_a_blank_separator():
    a = [10.0, 11.3, 12.5, 13.6, math.nan, 14.9, 16.2, 17.4, 18.7, 19.9, 21.2, math.nan, 22.6, 23.8]
    drift = [1.0, 1.1, 1.2, 1.3, 0.0, 1.4, 1.4, 1.4, 1.4, 1.4, 1.4, 0.0, 1.5, 1.6]
    b = [x + d if math.isfinite(x) else math.nan for x, d in zip(a, drift)]
    assert _best(a, [round(v, 1) if math.isfinite(v) else v for v in b]) is None


def test_a_stretch_whose_outside_barely_departs_is_not_reported():
    # the curve-family shape: the outside is off the constant by ~2%, far beyond rounding
    b = [round(x * 1.137, 3) for x in A10[:6]] + [round(x * 1.137 * 1.02, 3) for x in A10[6:]]
    assert _best(A10, b) is None


def test_a_formula_cached_cell_cannot_carry_the_score(monkeypatch):
    import paperconan._audit as audit_module
    a = [21.3, 22.8, 20.9, 23.4, 22.1, 21.7, 24.0, 22.6, 20.5, 23.1]
    b = [round(x + 3.0, 1) for x in a[:8]] + [22.0, 25.9]
    plain = _best(a, b)[2]
    a[3] += 1e-9          # one cell on each side stored at full float precision
    b[3] += 1e-9
    cap = audit_module._SHORT_ROW_MAX_QUANTUM_RATIO
    assert _best(a, b)[2] - plain <= math.log2(cap) + 1e-6
    monkeypatch.setattr(audit_module, "_SHORT_ROW_MAX_QUANTUM_RATIO", 1e12)
    assert _best(a, b)[2] - plain > math.log2(cap) + 1      # the cap is what bounds it


def test_a_whole_line_relation_with_a_source_rounded_to_zero_is_not_a_stretch():
    true = [0.04, 1.37, 2.81, 0.66, 1.92, 2.44, 0.83, 1.58, 2.17, 1.05, 2.69, 0.31]
    a, b = [round(x, 1) for x in true], [round(2.5 * x, 1) for x in true]
    assert a[0] == 0.0 and b[0] == 0.1
    assert _best(a, b) is None and _best(b, a) is None


def test_a_source_read_as_zero_that_the_constant_cannot_write_departs():
    a = [21.3, 22.8, 20.9, 23.4, 22.1, 21.7, 24.0, 22.6, 0.0]
    b = [round(x * 0.96, 3) for x in a[:8]] + [21.6]
    relation, group, _bits, departure = _best(a, b)
    assert relation == "ratio" and group["positions"] == list(range(8)) and departure > 0.05


def test_a_source_read_as_zero_inside_a_stretch_joins_it_when_the_constant_writes_it():
    true = [3.42, 1.87, 0.04, 2.95, 4.11, 3.06, 2.58, 1.73]
    a = [round(x, 1) for x in true] + [3.9, 2.2, 4.6, 1.4]
    b = [round(2.5 * x, 2) for x in true] + [5.3, 9.1, 3.2, 6.6]
    assert a[2] == 0.0 and b[2] == 0.1
    relation, group, _bits, departure = _best(a, b)
    assert relation == "ratio" and group["positions"] == list(range(8)) and departure > 0.05


def test_a_source_read_as_zero_that_the_constant_barely_misses_is_not_an_abrupt_edge():
    true = [3.42, 1.87, 2.95, 4.11, 3.06, 2.58, 1.73, 3.37]
    a = [round(x, 1) for x in true] + [0.0, 3.9, 2.2, 4.6]
    b = [round(2.5 * x, 2) for x in true] + [0.15, 5.3, 9.1, 3.2]
    assert _best(a, b) is None


def test_cells_that_confirm_nothing_do_not_make_scattered_consistency_a_stretch():
    a = [12.437, 8.912, 15.226, 9.871, 11.054, 13.388, 7.645, 10.902, 14.117, 9.333,
         10.051, 12.884, 9.4466, 13.1027, 11.7739]
    b = [round(x * 1.137, 3) for x in a]
    for i, v in ((3, 2.5), (7, 30.0), (11, 4.2)):
        b[i] = v
    assert _best([0.0] * 6 + a, [0.0] * 6 + b) is None          # zero against zero, six times


def test_a_stretch_is_reported_without_the_shared_zeros_beside_it():
    b = [round(x * 1.137, 3) for x in A10[:6]] + [11.9, 9.1, 18.7, 6.2]
    relation, group, _bits, _dep = _best([0.0] * 4 + A10, [0.0] * 4 + b)
    assert relation == "ratio" and group["positions"] == list(range(4, 10))


def test_a_pair_pays_a_pass_over_the_line_for_each_run_before_grouping():
    a, b = _arr(A10), _arr([round(x * 1.137, 3) for x in A10[:6]] + [11.9, 9.1, 18.7, 6.2])
    q_a, q_b = _effective_line_quantums(a), _effective_line_quantums(b)
    budget = [100]
    assert _best_local_group(a, b, q_a, q_b, 4, budget) is not None
    assert budget == [100 - len(A10)]          # one ratio run, no offset run
    budget = [len(A10) - 1]
    assert _best_local_group(a, b, q_a, q_b, 4, budget) is None and budget[0] < 0     # abandoned


def test_cells_that_confirm_nothing_inside_a_stretch_do_not_carry_scattered_consistency():
    a = [12.437, 8.912, 15.226, 9.871, 11.054, 13.388, 7.645, 10.902, 14.117, 9.333,
         10.051, 12.884, 9.4466, 13.1027, 11.7739]
    b = [round(x * 1.137, 3) for x in a]
    for i, v in ((3, 2.5), (7, 30.0), (11, 4.2)):
        b[i] = v
    for at in (1, 5, 9, 13):
        assert _best(a[:at] + [0.0] * 6 + a[at:], b[:at] + [0.0] * 6 + b[at:]) is None
    b = [round(x * 1.137, 3) for x in A10[:6]] + [11.9, 9.1, 18.7, 6.2]
    relation, group, _bits, _dep = _best(A10[:3] + [0.0] * 3 + A10[3:], b[:3] + [0.0] * 3 + b[3:])
    assert relation == "ratio" and group["segments"] == [[0, 8]]


def test_cells_that_confirm_nothing_set_neither_the_scale_nor_the_outside():
    b = [round(x * 1.137, 3) for x in A10[:6]] + [round(x * 1.137 * 1.02, 3) for x in A10[6:]]
    assert _best(A10 + [0.0] * 12, b + [0.0] * 12) is None
    b = [round(x * 1.137, 3) for x in A10[:6]] + [11.9, 9.1, 18.7, 6.2]
    relation, group, _bits, _dep = _best(A10 + [0.0] * 12, b + [0.0] * 12)
    assert relation == "ratio" and group["segments"] == [[0, 5]]


def test_a_groups_bits_are_summed_over_its_segments_each_against_its_own_spread():
    a, b = _arr(A10), _arr([round(x * 1.137, 3) for x in A10])
    q_a, q_b = _effective_line_quantums(a), _effective_line_quantums(b)
    whole = {"segments": [[0, 9]], "informative": list(range(10))}
    split = {"segments": [[0, 4], [5, 9]], "informative": list(range(10))}
    lone = {"segments": [[0, 0], [4, 4], [9, 9]], "informative": [0, 4, 9]}
    assert _local_group_bits(a, b, q_a, q_b, lone, 1) == 0.0          # each cell only anchors

    def by_segment(line, q):        # each segment against its own spread, as the docstring says
        return sum(_ratio_prediction_bits([line[i] for i in range(s, e + 1)], [q[i] for i in range(s, e + 1)], 1)
                   ["raw_bits"] for s, e in split["segments"])

    assert _local_group_bits(a, b, q_a, q_b, split, 1) == pytest.approx(min(by_segment(b, q_b), by_segment(a, q_a)))
    assert _local_group_bits(a, b, q_a, q_b, split, 1) < _local_group_bits(a, b, q_a, q_b, whole, 1)
    assert _local_group_bits(a, b, q_a, q_b, split, 8) == pytest.approx(_local_group_bits(a, b, q_a, q_b, split, 1) - 3)


def test_a_groups_bits_are_those_of_its_less_precise_side():
    a, coarse = _arr(A10), _arr([round(x * 1.137, 1) for x in A10])
    q_a, q_c = _effective_line_quantums(a), _effective_line_quantums(coarse)
    group = {"segments": [[0, 9]], "informative": list(range(10))}
    coarse_side = _ratio_prediction_bits(list(coarse), q_c, 1)["raw_bits"]
    assert _local_group_bits(a, coarse, q_a, q_c, group, 1) == pytest.approx(coarse_side)
    assert _local_group_bits(coarse, a, q_c, q_a, group, 1) == pytest.approx(coarse_side)


def test_a_source_read_as_zero_does_not_carry_scattered_consistency():
    a = [12.437, 8.912, 15.226, 9.871, 11.054, 13.388, 7.645, 10.902, 14.117, 9.333,
         10.051, 12.884, 9.447, 13.103, 11.774]
    b = [round(x * 1.137, 3) for x in a]
    for i, v in ((3, 2.5), (7, 30.0), (11, 4.2)):
        b[i] = v
    for at in (1, 5, 9, 13):        # six sources read as zero, each target one step above it
        assert _best(a[:at] + [0.0] * 6 + a[at:], b[:at] + [0.001] * 6 + b[at:]) is None


def test_a_groups_bits_score_only_its_informative_cells():
    a = _arr(A10[:4] + [0.0] + A10[4:])
    b = _arr([round(x * 1.137, 3) for x in A10[:4]] + [0.0] + [round(x * 1.137, 3) for x in A10[4:]])
    q_a, q_b = _effective_line_quantums(a), _effective_line_quantums(b)
    keep = [i for i in range(11) if i != 4]                   # the zero against zero informs nothing
    group = {"segments": [[0, 10]], "positions": list(range(11)), "informative": keep}
    expected = min(_ratio_prediction_bits([b[i] for i in keep], [q_b[i] for i in keep], 1)["raw_bits"],
                   _ratio_prediction_bits([a[i] for i in keep], [q_a[i] for i in keep], 1)["raw_bits"])
    assert _local_group_bits(a, b, q_a, q_b, group, 1) == pytest.approx(expected)


def test_a_pair_that_cannot_pay_for_its_runs_is_abandoned_before_grouping(monkeypatch):
    import paperconan._audit as audit_module

    def grouped(*args, **kwargs):
        raise AssertionError("grouped before paying for the runs")

    monkeypatch.setattr(audit_module, "_group_constant_runs", grouped)
    a, b = _arr(A10), _arr([round(x * 1.137, 3) for x in A10[:6]] + [11.9, 9.1, 18.7, 6.2])
    budget = [len(A10) - 1]
    assert _best_local_group(a, b, _effective_line_quantums(a), _effective_line_quantums(b), 4, budget) is None
    assert budget[0] < 0


def test_an_offset_exactly_ten_steps_from_the_identity_is_reported():
    rng = random.Random(20261018)
    for _ in range(20):           # 1.0 over a half-width of 0.1: on the lattice, so float error must not decide
        a = [round(rng.uniform(20.0, 25.0), 1) for _ in range(10)]
        b = [round(x + 1.0, 1) for x in a[:8]] + [round(a[8] + 3.7, 1), round(a[9] - 4.4, 1)]
        assert _best(a, b) is not None


def test_an_offset_nine_and_a_half_steps_from_the_identity_is_not_reported():
    rng = random.Random(20261021)
    for _ in range(20):           # the tolerance only takes float error: 0.95 over 0.1 is not ten steps
        a = [round(rng.uniform(20.0, 25.0), 1) for _ in range(10)]
        b = [round(x + d, 1) for x, d in zip(a[:8], [1.0, 0.9] * 4)] + [round(a[8] + 3.7, 1), round(a[9] - 4.4, 1)]
        assert _best(a, b) is None


def test_prediction_bits_exactly_at_the_threshold_are_enough():
    a = [13.6, 10.4, 11.5, 13.6, 10.4, 9.9, 19.6, 7.3, 9.4, 20.9]
    b = [37.639, 28.783, 31.827, 37.639, 28.783, 23.289, 49.875, 10.533, 13.05, 43.372]
    # a segment spreading over 32 of A's steps: four confirming cells at 5 bits each, exactly 20
    assert _best(a, b, n_tests=1) is not None


def test_an_edge_exactly_three_steps_off_is_abrupt_enough():
    rng = random.Random(20261019)
    for _ in range(20):           # 3.4 against 3.0 at 1 dp: 0.3 beyond rounding, in steps of 0.1
        a = [round(rng.uniform(20.0, 25.0), 1) for _ in range(11)]
        b = ([round(x + 3.0, 1) for x in a[:8]]
             + [round(a[8] + 3.4, 1), round(a[9] + 9.7, 1), round(a[10] - 4.2, 1)])
        assert _best(a, b) is not None


def test_an_outside_departing_by_exactly_the_threshold_is_not_local():
    rng = random.Random(20261020)
    for _ in range(20):           # four of six outside cells off by 0.2 over |c| = 4: a median of exactly 0.05
        a = [round(rng.uniform(20.0, 25.0), 1) for _ in range(14)]
        b = [round(x + o, 1) for x, o in zip(a, [4.0] * 8 + [9.1] + [4.3] * 4 + [11.3])]
        assert _best(a, b) is None


def test_an_offset_interval_holding_zero_is_the_identity():
    # integer cells pin the offset to [0, 1]; finer cells whose differences fall anywhere in it
    # would otherwise be scored at their own step
    a = [40.0, 48.0, 23.0, 60.0, 44.0, 34.0, 31.0, 71.0, 29.0, 34.788, 60.158, 68.552, 78.372, 80.404,
         21.0, 18.0, 12.0, 61.0, 80.0, 47.0]
    b = [17.0, 38.0, 76.0, 78.0, 44.0, 35.0, 32.0, 56.0, 45.0, 34.916, 60.753, 69.207, 78.877, 80.614,
         32.0, 23.0, 43.0, 37.0, 13.0, 43.0]
    assert _best(a, b) is None


@pytest.mark.parametrize("mirrored", [False, True])
def test_a_stretch_needs_an_abrupt_edge_at_both_ends(mirrored):
    a = [10.0, 11.3, 12.5, 13.6, 14.9, 16.2, 17.4, 18.7, 19.9, 21.2, 22.6, 23.8]
    d = [5.0, 9.1, 1.4, 1.4, 1.4, 1.4, 1.4, 1.4, 1.4, 1.5, 1.6, 1.7]      # abrupt on one side only
    if mirrored:
        a, d = a[::-1], d[::-1]
    assert _best(a, [round(x + e, 1) for x, e in zip(a, d)]) is None


def test_the_group_with_the_most_bits_is_chosen_over_the_longest():
    a = [10.412, 10.087, 10.733, 10.291, 10.958, 10.164, 10.645, 10.379, 3.1, 47.0, 22.9, 8.8, 39.4, 15.2,
         5.0, 42.0, 17.3]
    b = [round(x * 2.0, 3) for x in a[:8]] + [round(x * 0.6, 3) for x in a[8:14]] + [33.3, 1.7, 40.2]
    relation, group, _bits, _dep = _best(a, b)
    # eight cells of k = 2 over a narrow range carry less than six of k = 0.6 over a wide one
    assert relation == "ratio" and group["positions"] == list(range(8, 14))


def test_a_tie_between_the_arms_goes_to_the_ratio():
    a = [1000.067, 1000.424, 1000.382, 1000.128, 1000.248, 1000.225, 1000.326, 1000.394, 918.772, 905.669,
         1067.153, 986.553, 1052.456, 900.421]
    b = [1000.567, 1000.924, 1000.882, 1000.628, 1000.748, 1000.725, 1000.826, 1000.894, 989.077, 1044.308,
         945.752, 1089.054, 1080.285, 906.118]
    # both arms hold cells 0..7 and score the same cells, so the same bits
    assert _best(a, b)[0] == "ratio"


def test_a_pair_pays_once_for_every_run():
    a = _arr(A10 + [10.051, 12.884, 9.447, 13.103, 11.774, 8.25])
    b = _arr([round(x * 0.6, 3) for x in a[:5]] + [7.5, 4.0, 9.3, 7.1, 1.2, 15.0]
             + [round(x * 0.6, 3) for x in a[11:]])
    budget = [1000]
    _best_local_group(a, b, _effective_line_quantums(a), _effective_line_quantums(b), 4, budget)
    assert budget == [1000 - 2 * len(a)]          # two ratio runs, no offset run


def test_a_stretch_holding_exactly_half_of_its_positions_is_contiguous_enough():
    a = A10 + [10.051, 12.884, 9.447, 13.103, 11.774, 8.25]
    b = [round(x * 0.6, 3) for x in a[:5]] + [7.5, 4.0, 9.3, 7.1, 1.2, 15.0] + [round(x * 0.6, 3) for x in a[11:]]
    relation, group, _bits, _dep = _best(a, b)
    assert relation == "ratio" and group["segments"] == [[0, 4], [11, 15]]


def test_a_source_read_as_zero_is_admitted_by_any_constant_of_the_group():
    a = [7.7, 7.6, 1.4, 1.6, 6.8, 6.2, 5.7, 0.0, 3.2, 5.2, 5.2]
    b = [19.17, 18.92, 3.49, 3.98, 16.93, 15.44, 14.19, 0.13, 12.46, 4.85, 9.75]
    # 0.13 needs |k| of at least 2.5; the group's interval reaches it, though not its middle
    relation, group, _bits, _dep = _best(a, b, n_tests=1)
    assert relation == "ratio" and group["positions"] == list(range(8))


def test_two_independently_sorted_lines_are_not_a_stretch():
    rng = random.Random(20261017)
    a = sorted(round(rng.uniform(5.0, 20.0), 3) for _ in range(1000))
    b = sorted(round(rng.uniform(5.0, 20.0), 3) for _ in range(1000))
    assert _best(a, b, n_tests=2 * 1000) is None


def test_a_sign_flip_and_a_stretch_of_zeros_are_not_reported():
    # A sign-flipped copy: its interval holds -1, a power of ten, so it is left with the unit
    # restatements -- a recorded gap rather than a judgement. A stretch of zeros: its constant
    # is 0, which carries no information on the zero side and leaves no departure to measure;
    # not a relation this pass looks for, and three of its tests refuse it independently.
    assert _best(A10, [round(-x, 3) for x in A10[:6]] + [11.9, 9.1, 18.7, 6.2]) is None
    assert _best(A10, [0.0] * 6 + [11.9, 9.1, 18.7, 6.2]) is None


def test_a_zero_target_inside_a_stretch_is_scored_without_dividing_by_it():
    a = [100.37, 2.11, 85.42, 1.73, 63.18, 97.55, 2.46, 71.09, 55.31, 40.12, 33.87, 12.64]
    b = [round(0.002 * x, 2) for x in a[:9]] + [0.35, 0.91, 0.47]
    _best(a, b)          # must not raise


# --- the detector -------------------------------------------------------------------------

def _block(cols, header=None):
    header = header or [f"Group {chr(65 + j)}" for j in range(len(cols))]
    rows = [header] + [[c[i] for c in cols] for i in range(len(cols[0]))]
    return Sheet.from_rows(rows), header


def test_detector_reports_a_column_pair_with_its_cells_and_orientation():
    b = [round(x * 1.137, 3) for x in A10[:6]] + [11.9, 9.1, 18.7, 6.2]
    sheet, header = _block([A10, b])
    (f,) = detect_local_pair_runs(sheet, 1, 11, 0, 2, header)
    assert f["kind"] == "local_ratio_run" and f["orientation"] == "cols"
    assert (f["col_a_idx"], f["col_b_idx"]) == (0, 1)
    assert f["segments"] == [[2, 7]]                       # sheet rows, 1-based
    assert sorted({r for r, _ in f["example_cells"]}) == [2, 3, 4, 5, 6, 7]
    assert f["n"] == f["run_length"] == 6 and f["line_length"] == 10
    assert (f["col_a"], f["col_b"]) == ("Group A", "Group B") and f["severity"] == "high"
    assert f["k_lo"] <= f["k"] <= f["k_hi"] and f["k"] == pytest.approx(1.137, rel=1e-3)
    assert f["rule"].startswith("col[1] = col[0] * 1.13") and f["rule"].endswith("on rows 2-7")


def test_detector_reports_a_row_pair():
    b = [round(x * 1.137, 3) for x in A10[:6]] + [11.9, 9.1, 18.7, 6.2]
    rows = [["", *[f"t{j}" for j in range(10)]], ["ctrl", *A10], ["drug", *b]]
    sheet = Sheet.from_rows(rows)
    (f,) = detect_local_pair_runs(sheet, 1, 3, 1, 11, [f"t{j}" for j in range(10)])
    assert f["orientation"] == "rows" and (f["row_a_idx"], f["row_b_idx"]) == (1, 2)
    assert f["segments"] == [[2, 7]]                       # sheet columns, 1-based
    assert (f["row_a"], f["row_b"]) == ("ctrl", "drug")


def test_a_pair_already_reported_is_skipped():
    b = [round(x * 1.137, 3) for x in A10[:6]] + [11.9, 9.1, 18.7, 6.2]
    sheet, header = _block([A10, b])
    assert detect_local_pair_runs(sheet, 1, 11, 0, 2, header, already={("cols", 0, 1)}) == []


def test_skip_columns_leaves_only_rows():
    b = [round(x * 1.137, 3) for x in A10[:6]] + [11.9, 9.1, 18.7, 6.2]
    sheet, header = _block([A10, b])
    assert detect_local_pair_runs(sheet, 1, 11, 0, 2, header, skip_columns=True) == []
    sheet, r0, r1, c0, c1, labels = _row_pair_away_from_the_corner()
    (f,) = detect_local_pair_runs(sheet, r0, r1, c0, c1, labels, skip_columns=True)
    assert f["orientation"] == "rows"


def test_an_axis_row_is_not_compared():
    b = [round(x * 1.137, 3) for x in A10[:6]] + [11.9, 9.1, 18.7, 6.2]
    rows = [["", *[f"t{j}" for j in range(10)]], ["Time (h)", *A10], ["drug", *b]]
    sheet = Sheet.from_rows(rows)
    assert detect_local_pair_runs(sheet, 1, 3, 1, 11, [f"t{j}" for j in range(10)]) == []


def test_a_findings_n_counts_the_cells_the_relation_was_tested_on():
    b = [round(x * 1.137, 3) for x in A10[:6]] + [11.9, 9.1, 18.7, 6.2]
    sheet, header = _block([A10[:3] + [0.0] * 3 + A10[3:], b[:3] + [0.0] * 3 + b[3:]])
    (finding,) = detect_local_pair_runs(sheet, 1, 14, 0, 2, header)
    # three zeros against zeros sit inside the stretch and confirm nothing
    assert finding["run_length"] == 9 and finding["n"] == 6


def _row_pair_away_from_the_corner():
    """Rows 2 and 3 of a sheet whose data start at column 3, labelled in column 2."""
    b = [round(x * 1.137, 3) for x in A10[:6]] + [11.9, 9.1, 18.7, 6.2]
    labels = [f"t{j}" for j in range(10)]
    rows = [[None] * 13, [None, None, None, *labels], [None, None, "ctrl", *A10], [None, None, "drug", *b]]
    return Sheet.from_rows(rows), 2, 4, 3, 13, labels


def _read_back(sheet, finding, a, b):
    """Every example cell, read off the sheet, is a value of A or B at a position of the stretch."""
    wanted = {round(v, 9) for v in a + b}
    return all(round(float(sheet.cell(r - 1, c - 1)), 9) in wanted for r, c in finding["example_cells"])


def test_a_column_pair_away_from_the_corner_points_at_its_own_cells():
    b = [round(x * 1.137, 3) for x in A10[:6]] + [11.9, 9.1, 18.7, 6.2]
    rows = ([[None] * 6 for _ in range(3)] + [[None, None, None, None, "Group A", "Group B"]]
            + [[None, None, None, None, x, y] for x, y in zip(A10, b)])
    sheet = Sheet.from_rows(rows)
    (f,) = detect_local_pair_runs(sheet, 4, 14, 4, 6, ["Group A", "Group B"])
    assert (f["col_a_idx"], f["col_b_idx"]) == (4, 5) and f["segments"] == [[5, 10]]
    assert f["rule"].startswith("col[5] = col[4] *") and f["rule"].endswith("on rows 5-10")
    assert {c for _, c in f["example_cells"]} == {5, 6} and {r for r, _ in f["example_cells"]} == set(range(5, 11))
    assert _read_back(sheet, f, A10[:6], b[:6])


def test_a_row_pair_away_from_the_corner_points_at_its_own_cells():
    sheet, r0, r1, c0, c1, labels = _row_pair_away_from_the_corner()
    b = [round(x * 1.137, 3) for x in A10[:6]]
    (f,) = detect_local_pair_runs(sheet, r0, r1, c0, c1, labels)
    assert (f["row_a_idx"], f["row_b_idx"]) == (2, 3) and (f["row_a"], f["row_b"]) == ("ctrl", "drug")
    assert f["row_a_sample"][0] == pytest.approx(A10[0]) and f["row_b_sample"][0] == pytest.approx(b[0])
    assert f["segments"] == [[4, 9]]                        # sheet columns, 1-based
    assert f["rule"].startswith("row[4] = row[3] *") and f["rule"].endswith("on columns 4-9")
    assert {r for r, _ in f["example_cells"]} == {3, 4} and {c for _, c in f["example_cells"]} == set(range(4, 10))
    assert _read_back(sheet, f, A10[:6], b)


def test_an_offset_stretch_is_reported_as_one():
    b = [round(x + 3.517, 3) for x in A10[:6]] + [11.9, 9.1, 18.7, math.nan]
    sheet, header = _block([A10, b])
    (f,) = detect_local_pair_runs(sheet, 1, 11, 0, 2, header)
    assert f["line_length"] == 9                           # the blank in B is not part of the pair
    assert f["kind"] == "local_offset_run" and f["offset_lo"] <= 3.517 <= f["offset_hi"]
    assert f["rule"].startswith("col[1] = col[0] + 3.517")
    assert f["col_a_sample"][0] == pytest.approx(A10[0]) and f["col_b_sample"][0] == pytest.approx(b[0])


def test_a_pair_already_reported_in_one_orientation_is_compared_in_the_other():
    sheet, r0, r1, c0, c1, labels = _row_pair_away_from_the_corner()
    assert len(detect_local_pair_runs(sheet, r0, r1, c0, c1, labels, already={("cols", 2, 3)})) == 1


def test_the_search_penalty_counts_the_pairs_of_usable_lines():
    a, b = A10, [round(x * 1.137, 3) for x in A10[:6]] + [11.9, 9.1, 18.7, 6.2]
    alone = _best(a, b, n_tests=1 * 2 * 10)[2]           # one pair, two relations, ten cells
    sheet, header = _block([a, b])
    (f,) = detect_local_pair_runs(sheet, 1, 11, 0, 2, header)
    assert f["prediction_bits"] == round(alone, 2)
    c = [3.3, 17.1, 6.4, 12.2, 8.8, 15.5, 4.1, 19.6, 10.3, 7.7]
    sheet, header = _block([a, b, c])
    (f,) = [f for f in detect_local_pair_runs(sheet, 1, 11, 0, 3, header) if (f["col_a_idx"], f["col_b_idx"]) == (0, 1)]
    assert f["prediction_bits"] == round(alone - math.log2(3), 2)            # three pairs now
    sheet, header = _block([a, b, [float(i) for i in range(10)]])           # a grid is not compared
    (f,) = detect_local_pair_runs(sheet, 1, 11, 0, 3, header)
    assert f["prediction_bits"] == round(alone, 2)


def test_example_cells_are_bounded():
    rng = random.Random(20261022)
    a = [round(rng.uniform(5.0, 20.0), 3) for _ in range(100)]
    b = [round(x * 1.137, 3) for x in a[:80]] + [round(rng.uniform(5.0, 20.0), 3) for _ in range(20)]
    sheet, header = _block([a, b])
    (f,) = detect_local_pair_runs(sheet, 1, 101, 0, 2, header)
    assert len(f["example_cells"]) == 2 * _LOCAL_RUN_MAX_EXAMPLE_POSITIONS and f["example_cells_truncated"]


def test_on_the_whole_sheet_a_pair_inside_one_panel_is_left_to_that_panel():
    # two panels stacked in the same columns: B = 1.137 * A over all of the first, unrelated in
    # the second. Read over the whole sheet the second panel would make the first a stretch.
    first = [round(x * 1.137, 3) for x in A10]
    c = [3.3, 17.1, 6.4, 12.2, 8.8, 15.5, 4.1, 19.6, 10.3, 7.7]
    d = [12.6, 5.2, 18.4, 9.9, 14.1, 6.6, 11.3, 16.8, 7.4, 13.5]
    rows = [["Group A", "Group B"]] + [[x, y] for x, y in zip(A10, first)] + [[None, None]] + [[x, y] for x, y in zip(c, d)]
    sheet = Sheet.from_rows(rows)
    panels = [(1, 11, 0, 2), (12, 22, 0, 2)]
    assert detect_local_pair_runs(sheet, 1, 22, 0, 2, ["Group A", "Group B"]) != []      # the reading this prevents
    assert detect_local_pair_runs(sheet, 1, 22, 0, 2, ["Group A", "Group B"], panels=panels) == []


def test_on_the_whole_sheet_a_pair_across_panels_is_still_compared():
    # two panels side by side: a stretch of the first panel's column copied into the second's
    b = [round(x * 1.137, 3) for x in A10[:6]] + [11.9, 9.1, 18.7, 6.2]
    c = [3.3, 17.1, 6.4, 12.2, 8.8, 15.5, 4.1, 19.6, 10.3, 7.7]
    rows = [["Group A", "Group C", None, "Group B", "Group D"]] + [[x, z, None, y, w] for x, y, z, w in zip(A10, b, c, c[::-1])]
    sheet = Sheet.from_rows(rows)
    header = ["Group A", "Group C", "", "Group B", "Group D"]
    found = detect_local_pair_runs(sheet, 1, 11, 0, 5, header, panels=[(1, 11, 0, 2), (1, 11, 3, 5)])
    assert [(f["col_a_idx"], f["col_b_idx"]) for f in found] == [(0, 3)]


def test_on_the_whole_sheet_a_panel_holding_both_lines_leaves_out_only_its_own_positions():
    # side by side, then a panel across the full width below: it holds columns 0 and 3, but only
    # over its own rows -- where the first two panels meet, no panel compared those columns
    b = [round(x * 1.137, 3) for x in A10[:6]] + [11.9, 9.1, 18.7, 6.2]
    c = [3.3, 17.1, 6.4, 12.2, 8.8, 15.5, 4.1, 19.6, 10.3, 7.7]
    rng = random.Random(20261023)
    below = [[round(rng.uniform(5.0, 20.0), 3) for _ in range(5)] for _ in range(10)]
    for row in below[::2]:
        row[3] = 0.0                     # zeros in B where the full-width panel holds both columns
    rows = ([["Group A", "Group C", None, "Group B", "Group D"]] + [[x, z, None, y, w] for x, y, z, w in zip(A10, b, c, c[::-1])]
            + [[None] * 5, [f"x{j}" for j in range(5)]] + below)
    sheet = Sheet.from_rows(rows)
    header = ["Group A", "Group C", "", "Group B", "Group D"]
    panels = [(1, 11, 0, 2), (1, 11, 3, 5), (13, 23, 0, 5)]
    found = detect_local_pair_runs(sheet, 1, 23, 0, 5, header, panels=panels)
    assert [(f["col_a_idx"], f["col_b_idx"], f["segments"]) for f in found] == [(0, 3, [[2, 7]])]
    assert found[0]["run_length"] == 6 and found[0]["line_length"] == 10     # read over what is left


def test_on_the_whole_sheet_rows_of_two_panels_are_compared_where_no_panel_holds_both():
    # a row of one panel above a row of another, and a third panel to their right spanning both
    b = [round(x * 1.137, 3) for x in A10[:6]] + [11.9, 9.1, 18.7, 6.2]
    rng = random.Random(20261024)
    side = [[round(rng.uniform(5.0, 20.0), 3) for _ in range(3)] for _ in range(3)]
    rows = ([[None, *[f"t{j}" for j in range(10)], None, "u0", "u1", "u2"]]
            + [["ctrl", *A10, None, *side[0]], [None] * 11 + [None, *side[1]], ["drug", *b, None, *side[2]]])
    sheet = Sheet.from_rows(rows)
    panels = [(1, 2, 1, 11), (3, 4, 1, 11), (1, 4, 12, 15)]
    found = detect_local_pair_runs(sheet, 1, 4, 1, 15, [""] * 14, panels=panels)
    assert [(f["orientation"], f["row_a_idx"], f["row_b_idx"]) for f in found] == [("rows", 1, 3)]


def test_on_the_whole_sheet_panels_that_touch_do_not_hold_each_others_edge_rows():
    # two panels with no row between them: the last row of one and the first of the next meet
    # only on the whole sheet
    b = [round(x * 1.137, 3) for x in A10[:6]] + [11.9, 9.1, 18.7, 6.2]
    c = [3.3, 17.1, 6.4, 12.2, 8.8, 15.5, 4.1, 19.6, 10.3, 7.7]
    rows = [[None, *[f"t{j}" for j in range(10)]], ["p", *c], ["ctrl", *A10], ["drug", *b], ["q", *c[::-1]]]
    sheet = Sheet.from_rows(rows)
    panels = [(1, 3, 1, 11), (3, 5, 1, 11)]
    found = detect_local_pair_runs(sheet, 1, 5, 1, 11, [""] * 10, panels=panels, skip_columns=True)
    assert [(f["row_a_idx"], f["row_b_idx"]) for f in found] == [(2, 3)]


def test_on_the_whole_sheet_what_is_left_of_a_grid_is_still_a_grid():
    # the full-width panel below makes the first column look like data over the whole sheet;
    # over the rows left to compare it is a dilution series again
    ladder = [round(100.0 / 2 ** i, 4) for i in range(10)]
    b = [round(0.731 * x, 4) for x in ladder[:6]] + [11.9, 9.1, 18.7, 6.2]
    c = [3.3, 17.1, 6.4, 12.2, 8.8, 15.5, 4.1, 19.6, 10.3, 7.7]
    rng = random.Random(20261025)
    below = [[round(rng.uniform(5.0, 20.0), 3) for _ in range(5)] for _ in range(10)]
    rows = ([["Dose", "Group C", None, "Group B", "Group D"]] + [[x, z, None, y, w] for x, y, z, w in zip(ladder, b, c, c[::-1])]
            + [[None] * 5, [f"x{j}" for j in range(5)]] + below)
    sheet = Sheet.from_rows(rows)
    header = ["Dose", "Group C", "", "Group B", "Group D"]
    panels = [(1, 11, 0, 2), (1, 11, 3, 5), (13, 23, 0, 5)]
    found = detect_local_pair_runs(sheet, 1, 23, 0, 5, header, panels=panels)
    assert [f for f in found if (f["col_a_idx"], f["col_b_idx"]) == (0, 3)] == []
    # the same with the series in B: each line is judged on what is left of it
    a = [round(x / 0.731, 4) for x in ladder[:6]] + [11.9, 9.1, 18.7, 6.2]
    rows = ([["Group A", "Group C", None, "Dose", "Group D"]] + [[x, z, None, y, w] for x, y, z, w in zip(a, ladder, c, c[::-1])]
            + [[None] * 5, [f"x{j}" for j in range(5)]] + below)
    found = detect_local_pair_runs(Sheet.from_rows(rows), 1, 23, 0, 5, ["Group A", "Group C", "", "Dose", "Group D"], panels=panels)
    assert [f for f in found if (f["col_a_idx"], f["col_b_idx"]) == (0, 3)] == []


def test_on_the_whole_sheet_a_panel_leaves_out_exactly_its_own_rows():
    # a full-width panel directly above two side by side; its last rows hold the same constant
    rng = random.Random(20261026)
    top = [[round(rng.uniform(5.0, 20.0), 3) for _ in range(5)] for _ in range(5)]
    for row in top[3:]:
        row[3] = round(row[0] * 1.137, 3)
    b = [round(x * 1.137, 3) for x in A10[:6]] + [11.9, 9.1, 18.7, 6.2]
    c = [3.3, 17.1, 6.4, 12.2, 8.8, 15.5, 4.1, 19.6, 10.3, 7.7]
    rows = [["Group A", "Group C", None, "Group B", "Group D"]] + top + [[x, z, None, y, w] for x, y, z, w in zip(A10, b, c, c[::-1])]
    sheet = Sheet.from_rows(rows)
    header = ["Group A", "Group C", "", "Group B", "Group D"]
    panels = [(1, 6, 0, 5), (6, 16, 0, 2), (6, 16, 3, 5)]
    found = detect_local_pair_runs(sheet, 1, 16, 0, 5, header, panels=panels)
    assert [(f["col_a_idx"], f["col_b_idx"], f["segments"]) for f in found] == [(0, 3, [[7, 12]])]


def test_on_the_whole_sheet_a_pair_with_nothing_left_costs_nothing(monkeypatch):
    import paperconan._audit as audit_module
    first = [round(x * 1.137, 3) for x in A10]
    c = [3.3, 17.1, 6.4, 12.2, 8.8, 15.5, 4.1, 19.6, 10.3, 7.7]
    d = [12.6, 5.2, 18.4, 9.9, 14.1, 6.6, 11.3, 16.8, 7.4, 13.5]
    rows = [["Group A", "Group B"]] + [[x, y] for x, y in zip(A10, first)] + [[None, None]] + [[x, y] for x, y in zip(c, d)]
    sheet = Sheet.from_rows(rows)
    monkeypatch.setattr(audit_module, "_LOCAL_RUN_BUDGET", 5)       # less than one scan of the extent
    cov = _Coverage()
    found = detect_local_pair_runs(sheet, 1, 22, 0, 2, ["Group A", "Group B"], coverage=cov,
                                   panels=[(1, 11, 0, 2), (12, 22, 0, 2)])
    assert found == [] and not cov.calls
    # and the pair after it gets the whole budget: a third column, in no panel with the first,
    # holds a stretch of it; the budget is that pair's cost and no more
    e = [round(x * 1.137, 3) for x in A10[:6]] + [11.9, 9.1, 18.7, 6.2] + [None] + [5.5, 13.2, 8.1, 16.4, 10.9, 7.2, 14.8, 9.6, 12.3, 6.8]
    rows = [["Group A", "Group B", "Group E"]] + [[x, y, z] for x, y, z in zip(A10 + [None] + c, first + [None] + d, e)]
    sheet = Sheet.from_rows(rows)
    a_line, e_line = sheet.numeric[1:22, 0].astype(float), sheet.numeric[1:22, 2].astype(float)
    spent = [10 ** 9]
    _best_local_group(a_line, e_line, _effective_line_quantums(a_line), _effective_line_quantums(e_line), 1, spent)
    monkeypatch.setattr(audit_module, "_LOCAL_RUN_BUDGET", 21 + (10 ** 9 - spent[0]))
    found = detect_local_pair_runs(sheet, 1, 22, 0, 3, ["Group A", "Group B", "Group E"],
                                   panels=[(1, 11, 0, 2), (12, 22, 0, 2), (1, 22, 2, 3)])
    assert [(f["col_a_idx"], f["col_b_idx"]) for f in found] == [(0, 2)]


def test_a_pair_whose_lines_never_meet_costs_nothing(monkeypatch):
    import paperconan._audit as audit_module
    monkeypatch.setattr(audit_module, "_LOCAL_RUN_BUDGET", 5)
    c = [3.3, 17.1, 6.4, 12.2, 8.8, 15.5, 4.1, 19.6, 10.3, 7.7]
    # each line usable on its own, but never with a value where the other has one
    rows = [["Group A", "Group B"]] + [[x, None] for x in A10[:5]] + [[None, y] for y in c[:5]]
    sheet = Sheet.from_rows(rows)
    cov = _Coverage()
    assert detect_local_pair_runs(sheet, 1, 11, 0, 2, ["Group A", "Group B"], coverage=cov) == [] and not cov.calls
    # three cells with both values: still one short of a stretch and its outside
    meet = [["Group A", "Group B"]] + [[x, None] for x in A10[:4]] + [[x, y] for x, y in zip(A10[4:7], c[4:7])] + [[None, y] for y in c[7:]]
    cov = _Coverage()
    assert detect_local_pair_runs(Sheet.from_rows(meet), 1, 11, 0, 2, ["Group A", "Group B"], coverage=cov) == [] and not cov.calls
    # the same once a panel holding both lines is left out of the whole sheet
    rows += [[None, None]] + [[x, y] for x, y in zip(A10, c)]
    sheet = Sheet.from_rows(rows)
    cov = _Coverage()
    found = detect_local_pair_runs(sheet, 1, 22, 0, 2, ["Group A", "Group B"], coverage=cov, panels=[(12, 22, 0, 2)])
    assert found == [] and not cov.calls
    # and the pair after it gets the whole budget: a third column holds a stretch of the first,
    # and the budget is that pair's cost and no more
    a = A10[:6] + [None] * 4
    b = [None] * 6 + c[6:]
    e = [round(x * 1.137, 3) for x in A10[:5]] + [11.9] + [5.5, 13.2, 8.1, 16.4]
    sheet = Sheet.from_rows([["Group A", "Group B", "Group E"]] + [[x, y, z] for x, y, z in zip(a, b, e)])
    a_line, e_line = sheet.numeric[1:11, 0].astype(float), sheet.numeric[1:11, 2].astype(float)
    spent = [10 ** 9]
    _best_local_group(a_line, e_line, _effective_line_quantums(a_line), _effective_line_quantums(e_line), 1, spent)
    monkeypatch.setattr(audit_module, "_LOCAL_RUN_BUDGET", 10 + (10 ** 9 - spent[0]))
    found = detect_local_pair_runs(sheet, 1, 11, 0, 3, ["Group A", "Group B", "Group E"])
    assert [(f["col_a_idx"], f["col_b_idx"]) for f in found] == [(0, 2)]


def test_four_cells_with_both_values_can_hold_a_stretch_and_its_outside():
    a = [5.123, 15.456, 25.789, 12.345, 8.765, 19.432, None, None]
    b = [round(x * 1.137, 3) for x in a[:3]] + [30.111, None, None, 7.654, 22.987]
    (f,) = detect_local_pair_runs(Sheet.from_rows([["Group A", "Group B"]] + [[x, y] for x, y in zip(a, b)]),
                                  1, 9, 0, 2, ["Group A", "Group B"])
    assert f["segments"] == [[2, 4]] and f["line_length"] == 4


def test_a_block_taller_than_the_row_limit_is_compared_by_columns_only(monkeypatch):
    import paperconan._audit as audit_module
    monkeypatch.setattr(audit_module, "_LOCAL_RUN_MAX_ROWS", 5)
    b = [round(x * 1.137, 3) for x in A10[:6]] + [11.9, 9.1, 18.7, 6.2]
    c = [3.3, 17.1, 6.4, 12.2, 8.8, 15.5, 4.1, 19.6, 10.3, 7.7]
    sheet, header = _block([A10, b, c, c[::-1]])            # rows of four cells could be compared
    cov = _Coverage()
    found = detect_local_pair_runs(sheet, 1, 11, 0, 4, header, coverage=cov)
    assert {f["orientation"] for f in found} == {"cols"}
    assert [(args[1], kwargs["detector"]) for args, kwargs in cov.calls] == [("detector_row_limit", "detect_local_pair_runs")]
    sheet, header = _block([A10, b])                        # rows of two cells never could
    cov = _Coverage()
    assert len(detect_local_pair_runs(sheet, 1, 11, 0, 2, header, coverage=cov)) == 1 and not cov.calls
    sheet, header = _block([A10, b, c, [c[0]] + [math.nan] * 9])     # one row could; a pair of rows could not
    cov = _Coverage()
    detect_local_pair_runs(sheet, 1, 11, 0, 4, header, coverage=cov)
    assert not cov.calls


def test_a_block_exactly_at_the_row_limit_is_compared_by_rows(monkeypatch):
    import paperconan._audit as audit_module
    sheet, r0, r1, c0, c1, labels = _row_pair_away_from_the_corner()
    monkeypatch.setattr(audit_module, "_LOCAL_RUN_MAX_ROWS", r1 - r0)
    assert len(detect_local_pair_runs(sheet, r0, r1, c0, c1, labels)) == 1


def test_a_pair_already_reported_costs_nothing(monkeypatch):
    import paperconan._audit as audit_module
    b = [round(x * 1.137, 3) for x in A10[:6]] + [11.9, 9.1, 18.7, 6.2]
    c = [3.3, 17.1, 6.4, 12.2, 8.8, 15.5, 4.1, 19.6, 10.3, 7.7]
    sheet, header = _block([c, A10, b])
    monkeypatch.setattr(audit_module, "_LOCAL_RUN_BUDGET", 20)     # the one compared pair: its scan and its run
    cov = _Coverage()
    found = detect_local_pair_runs(sheet, 1, 11, 0, 3, header, already={("cols", 0, 1), ("cols", 0, 2)}, coverage=cov)
    assert len(found) == 1 and not cov.calls


def test_the_rule_lists_the_runs_and_counts_the_lone_positions():
    assert _stretch_text([[2, 7]]) == "2-7"
    assert _stretch_text([[2, 3], [9, 9], [12, 15], [20, 20]]) == "2-3, 12-15 (+2 scattered)"
    assert _stretch_text([[1, 2], [4, 5], [7, 8], [10, 11], [13, 14]]) == "1-2, 4-5, 7-8, 10-11 and 1 more"
    assert _stretch_text([[3, 3], [6, 6]]) == "3, 6"


def test_a_header_shorter_than_the_block_leaves_a_label_empty():
    b = [round(x * 1.137, 3) for x in A10[:6]] + [11.9, 9.1, 18.7, 6.2]
    sheet, _header = _block([A10, b])
    (f,) = detect_local_pair_runs(sheet, 1, 11, 0, 2, ["Group A"])
    assert (f["col_a"], f["col_b"]) == ("Group A", "")
    (f,) = detect_local_pair_runs(sheet, 1, 11, 0, 2, [None, "Group B"])
    assert (f["col_a"], f["col_b"]) == ("", "Group B")


class _Coverage:
    def __init__(self):
        self.calls = []

    def add_limitation(self, *args, **kwargs):
        self.calls.append((args, kwargs))


def test_an_exhausted_budget_is_recorded(monkeypatch):
    import paperconan._audit as audit_module
    b = [round(x * 1.137, 3) for x in A10[:6]] + [11.9, 9.1, 18.7, 6.2]
    sheet, header = _block([A10, b])
    for budget in (5, 15):          # the scan cannot be paid for; the scan can, its one run cannot
        monkeypatch.setattr(audit_module, "_LOCAL_RUN_BUDGET", budget)
        cov = _Coverage()
        assert detect_local_pair_runs(sheet, 1, 11, 0, 2, header, coverage=cov) == []
        assert cov.calls and cov.calls[0][1]["detector"] == "detect_local_pair_runs"


def test_the_run_work_of_a_pair_is_charged_to_the_blocks_budget(monkeypatch):
    import paperconan._audit as audit_module
    b = [round(x * 1.137, 3) for x in A10[:6]] + [11.9, 9.1, 18.7, 6.2]
    c = [3.3, 17.1, 6.4, 12.2, 8.8, 15.5, 4.1, 19.6, 10.3, 7.7]
    sheet, header = _block([A10, b, c])
    # three column pairs pay 10 each for the scan; the first pair's one run pays 10 more
    for budget, exhausted in ((41, False), (31, True)):
        monkeypatch.setattr(audit_module, "_LOCAL_RUN_BUDGET", budget)
        cov = _Coverage()
        assert len(detect_local_pair_runs(sheet, 1, 11, 0, 3, header, coverage=cov)) == 1
        assert bool(cov.calls) is exhausted


def test_a_pair_that_spends_the_budget_exactly_keeps_its_finding(monkeypatch):
    import paperconan._audit as audit_module
    b = [round(x * 1.137, 3) for x in A10[:6]] + [11.9, 9.1, 18.7, 6.2]
    sheet, header = _block([A10, b])
    monkeypatch.setattr(audit_module, "_LOCAL_RUN_BUDGET", 20)     # 10 for the scan, 10 for its one run
    cov = _Coverage()
    assert len(detect_local_pair_runs(sheet, 1, 11, 0, 2, header, coverage=cov)) == 1 and not cov.calls


def test_a_pair_whose_scan_spends_the_budget_exactly_is_scanned(monkeypatch):
    import paperconan._audit as audit_module
    c = [3.3, 17.1, 6.4, 12.2, 8.8, 15.5, 4.1, 19.6, 10.3, 7.7]
    sheet, header = _block([A10, c])
    monkeypatch.setattr(audit_module, "_LOCAL_RUN_BUDGET", 10)     # the scan, and no run to pay for
    cov = _Coverage()
    assert detect_local_pair_runs(sheet, 1, 11, 0, 2, header, coverage=cov) == [] and not cov.calls
