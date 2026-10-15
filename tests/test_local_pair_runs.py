"""Local pair runs: two lines of one block related as B = k*A or B = A + c over part of the
line only (docs/superpowers/specs/2026-10-15-local-pair-runs-design.md). Synthetic data only."""
import math
import random

import numpy as np
import pytest

from paperconan._sheet import Sheet
from paperconan._audit import (
    _LOCAL_RUN_DEPARTURE_CAP, _edge_steps, _effective_line_quantums, _effective_row_quantums,
    _group_constant_runs, _identity_steps, _local_line_usable, _outside_departure,
    _scan_interval_runs, _segments,
    _symmetric_offset_interval, _symmetric_ratio_interval, _zero_source_miss,
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
