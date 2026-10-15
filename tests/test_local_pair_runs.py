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


def test_zero_and_non_finite_cells_under_significant_figures():
    q = _effective_line_quantums([0.0, 234.5678, 45.67891, 56.78912, 67.89123, math.nan])
    assert q[0] == pytest.approx(1.0)
    assert math.isnan(q[-1])
