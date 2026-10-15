"""Local pair runs: two lines of one block related as B = k*A or B = A + c over part of the
line only (docs/superpowers/specs/2026-10-15-local-pair-runs-design.md). Synthetic data only."""
import math
import random

import numpy as np
import pytest

from paperconan._sheet import Sheet
from paperconan._audit import (
    _effective_line_quantums, _effective_row_quantums, _group_constant_runs,
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
    assert (run["start"], run["end"]) == (0, 2) and run["informative"] == [0, 2]


def test_runs_of_one_constant_form_one_group_with_the_lone_cells_between():
    spans = [(1.9, 2.1)] * 3 + [(5.0, 6.0), (1.95, 2.05), (7.0, 8.0)] + [(1.9, 2.1)] * 3
    runs = _scan_interval_runs(spans, list(range(9)))
    (group, *others) = _group_constant_runs(runs, spans)
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
