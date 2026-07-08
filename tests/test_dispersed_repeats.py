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


def test_adjacent_technical_replicates_do_not_fire():
    # 60 distinct values (passes the distinct/support gates) but each repeated in
    # ADJACENT rows (fill-down / technical replicate) -> dispersion guard must reject.
    col = []
    rng = np.random.default_rng(1)
    for _ in range(60):
        v = round(float(rng.uniform(1, 599)), 2)
        col += [v, v, v]   # 3 adjacent copies
    out = _detect(col)
    assert not [f for f in out if f["kind"] == "within_col_dispersed_repeats"]


def test_censored_ceiling_does_not_fire():
    # a continuous column dominated by a 600s ceiling, rest unique -> benign
    rng = np.random.default_rng(2)
    col = [600.0] * 90 + [round(float(rng.uniform(1, 599)), 2) for _ in range(60)]
    out = _detect(col)
    assert not [f for f in out if f["kind"] == "within_col_dispersed_repeats"]


def test_small_integer_column_does_not_fire():
    rng = np.random.default_rng(3)
    col = [int(rng.integers(0, 8)) for _ in range(150)]
    out = _detect(col)
    assert not [f for f in out if f["kind"] == "within_col_dispersed_repeats"]


def test_low_cardinality_ratio_column_does_not_fire():
    # ratios of small integers -> few distinct, naturally collide -> benign
    denoms = [2, 3, 4, 5, 6]
    rng = np.random.default_rng(4)
    col = [round(int(rng.integers(0, d + 1)) / d, 4) for d in
           (denoms[int(rng.integers(0, 5))] for _ in range(200))]
    out = _detect(col)
    assert not [f for f in out if f["kind"] == "within_col_dispersed_repeats"]


def test_monte_carlo_continuous_columns_have_near_zero_fp():
    # Genuinely continuous columns (varied precision/scale/n) must almost never fire.
    fp = 0
    trials = 200
    for seed in range(trials):
        rng = np.random.default_rng(1000 + seed)
        n = int(rng.integers(40, 400))
        scale = float(rng.choice([1.0, 10.0, 100.0, 600.0]))
        decimals = int(rng.choice([2, 3, 4]))
        col = [round(float(rng.uniform(0, scale)), decimals) for _ in range(n)]
        out = _detect(col)
        if [f for f in out if f["kind"] == "within_col_dispersed_repeats"]:
            fp += 1
    assert fp <= 2, f"false-positive rate too high: {fp}/{trials}"
