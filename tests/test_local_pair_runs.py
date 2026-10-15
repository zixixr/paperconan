"""Local pair runs: two lines of one block related as B = k*A or B = A + c over part of the
line only (docs/superpowers/specs/2026-10-15-local-pair-runs-design.md). Synthetic data only."""
import math

import numpy as np
import pytest

from paperconan._sheet import Sheet
from paperconan._audit import _effective_line_quantums, _effective_row_quantums

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
