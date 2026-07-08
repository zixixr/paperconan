"""within_col_dispersed_repeats: many DISTINCT high-precision values each
repeated across DISPERSED rows (Laskowski/Pruitt fingerprint), with FP guards."""
import numpy as np
import pytest
from paperconan._sheet import Sheet
from paperconan._audit import detect_dispersed_repeats


def _sheet(col, header="boldness"):
    rows = [[header]] + [[v] for v in col]
    return Sheet.from_rows(rows)


def _detect(col, header="boldness"):
    s = _sheet(col, header)
    return detect_dispersed_repeats(s, 1, len(col) + 1, 0, 1, [header])


def test_dispersed_high_precision_repeats_fire():
    # 60-row continuous-looking column (2-decimal latencies). Inject 12 distinct
    # values, each appearing 3x at DISPERSED (non-adjacent, wide-span) rows.
    rng = np.random.default_rng(7)
    col = [round(float(rng.uniform(1, 599)), 2) for _ in range(120)]
    injected = [round(float(rng.uniform(1, 599)), 2) for _ in range(12)]
    # scatter each injected value across the column at spread positions
    for i, val in enumerate(injected):
        for slot in (i, 40 + i, 80 + i):   # spans > half the column, non-adjacent
            col[slot] = val
    out = _detect(col)
    hits = [f for f in out if f["kind"] == "within_col_dispersed_repeats"]
    assert hits, "expected a dispersed-repeats finding"
    f = hits[0]
    assert f["col_idx"] == 0
    assert f["severity"] == "medium"
    assert f["n_repeat_groups"] >= 10
    # example_cells (1-based row,col) must point at injected duplicate rows
    assert f["example_cells"], "expected example_cells for the evidence heatmap"
    assert all(c == 1 for _, c in f["example_cells"])
