"""B3: detect_within_sheet_fraction_reuse — two numeric blocks in the SAME sheet whose
positionally-corresponding cells reproduce each other's high-precision decimal fractions
while integer parts differ by whole numbers (e.g. two dose-response matrices where every
cell was shifted by an integer). Includes a brute-force oracle."""
from __future__ import annotations

from paperconan._audit import Sheet, detect_within_sheet_fraction_reuse


def _sheet(rows):
    return Sheet.from_rows(rows)


def _matrix_block(base, shifts):
    """A 7x7 matrix `base` and a copy with each cell shifted by an integer from `shifts`
    (fractions preserved). Returns (block_a_rows, block_b_rows)."""
    a = [[round(base[r][c], 5) for c in range(len(base[r]))] for r in range(len(base))]
    b = [[round(base[r][c] + shifts[r][c], 5) for c in range(len(base[r]))] for r in range(len(base))]
    return a, b


def _grid_sheets_two_blocks(a_rows, b_rows, sheet="Figure 4a"):
    rows = [["PDO #4"]]
    rows += [list(r) for r in a_rows]
    rows += [[], [], []]                       # blank separator → two distinct numeric blocks
    rows += [["PDO #5"]]
    rows += [list(r) for r in b_rows]
    return {("MOESM.xls", sheet): _sheet(rows)}


def _b3_oracle(a_rows, b_rows):
    """Independent ground truth: >=80% of positionally-corresponding cells share the exact
    fraction (integer diff), >=half are high-precision (>=3 frac digits), >=3 real integer
    diffs with >=2 distinct, >=5 distinct fractions."""
    def sig(v):
        fv = abs(v - round(v))
        return 0 if fv < 1e-9 else len(f"{fv:.9f}".split(".")[1].rstrip("0"))
    common = [(r, c) for r in range(len(a_rows)) for c in range(len(a_rows[r]))]
    shared = intd = hp = 0
    fracs, diffset = set(), set()
    for r, c in common:
        x, y = a_rows[r][c], b_rows[r][c]
        d = y - x
        if abs(d - round(d)) < 1e-6 * max(abs(x), abs(y), 1):
            shared += 1
            if abs(round(d)) >= 1:
                intd += 1
                diffset.add(round(d))
            if sig(x) >= 3:
                hp += 1
                fracs.add(round(x - round(x), 6))
    return (shared >= max(10, round(0.8 * len(common)))
            and hp >= max(6, round(0.5 * len(common)))
            and intd >= 3 and len(diffset) >= 2 and len(fracs) >= 5)


_BASE = [[100.0, 102.97455, 95.37904, 92.56855, 88.78276, 80.73111, 75.00836],
         [101.56969, 95.27801, 80.68211, 85.27611, 70.24915, 53.33536, 54.76491],
         [97.26548, 90.07482, 75.50361, 73.03532, 60.19711, 50.77668, 40.61223],
         [85.12495, 70.07330, 72.36308, 56.32510, 43.75123, 30.15572, 29.66198],
         [75.05963, 62.18572, 62.06874, 53.32359, 31.38321, 20.64907, 20.04140],
         [68.84352, 60.06988, 50.92898, 33.00418, 22.10482, 19.34523, 13.11736],
         [63.86783, 58.85378, 48.29548, 25.45955, 16.30725, 13.91493, 12.18078]]

# integer shifts applied to make PDO#5 (varying whole numbers, some 0 → a few fully-identical cells)
_SHIFTS = [[0, -3, -1, -1, -2, 0, 3],
           [-1, 1, 8, 0, 8, 23, 14],
           [0, 2, 5, 6, 8, 5, 5],
           [2, 5, -14, 12, -9, 0, 0],
           [-4, -2, -19, -10, -11, 1, 2],
           [-3, -8, -12, 0, 0, 0, 5],
           [-3, -20, -18, 3, -1, -3, -4]]


def test_b3_flags_matrix_fraction_reuse_and_matches_oracle():
    a_rows, b_rows = _matrix_block(_BASE, _SHIFTS)
    gs = _grid_sheets_two_blocks(a_rows, b_rows)
    f = detect_within_sheet_fraction_reuse(gs)
    hi = [x for x in f if x["kind"] == "within_table_fraction_reuse" and x["severity"] == "high"]
    assert len(hi) == 1, f
    assert hi[0]["same_position_count"] >= 40
    assert _b3_oracle(a_rows, b_rows) is True


def test_b3_no_flag_when_blocks_are_independent():
    a_rows, _ = _matrix_block(_BASE, _SHIFTS)
    # a genuinely different second matrix (not integer-shifted; distinct fractions)
    b_rows = [[round(v * 0.837 + 1.1119, 5) for v in row] for row in a_rows]
    gs = _grid_sheets_two_blocks(a_rows, b_rows)
    f = detect_within_sheet_fraction_reuse(gs)
    assert not [x for x in f if x["severity"] == "high"]
    assert _b3_oracle(a_rows, b_rows) is False


def test_b3_no_flag_when_one_large_integer_column_inflates_block_scale():
    # regression (M2-1): a large-integer column (e.g. distanceToTSS up to ~3.6e6) must not
    # inflate the block-wide tolerance (1e-6 * block_max ~= 3.6) so that every independent
    # fractional cell reads as an integer-difference shared fraction. Two GENUINELY INDEPENDENT
    # fractional matrices that merely happen to include a big-integer coordinate column must NOT
    # flag; a per-cell tolerance keeps the fractional cells honest.
    a_rows, _ = _matrix_block(_BASE, _SHIFTS)
    b_rows = [[round(v * 0.837 + 1.1119, 5) for v in row] for row in a_rows]   # independent
    dist_a = [37.0, 128000.0, 3600000.0, 4200.0, 990000.0, 17.0, 250000.0]
    dist_b = [512.0, 44000.0, 1800000.0, 63.0, 770000.0, 209.0, 130000.0]
    a_rows = [list(r) + [dist_a[i]] for i, r in enumerate(a_rows)]
    b_rows = [list(r) + [dist_b[i]] for i, r in enumerate(b_rows)]
    gs = _grid_sheets_two_blocks(a_rows, b_rows)
    f = detect_within_sheet_fraction_reuse(gs)
    assert not [x for x in f if x["severity"] == "high"], f
    assert _b3_oracle(a_rows, b_rows) is False


def test_b3_no_flag_on_low_precision_half_grid():
    base = [[round((r * 7 + c) * 0.5, 1) for c in range(7)] for r in range(7)]   # .0/.5 only
    shifts = [[(r + c) % 5 for c in range(7)] for r in range(7)]
    a_rows, b_rows = _matrix_block(base, shifts)
    gs = _grid_sheets_two_blocks(a_rows, b_rows)
    f = detect_within_sheet_fraction_reuse(gs)
    assert not [x for x in f if x["severity"] == "high"]
    assert _b3_oracle(a_rows, b_rows) is False


def test_b3_finding_fields_are_internally_consistent():
    # regression: B3 set figure_a==figure_b (both figure_key(sheet)) but same_figure=False —
    # contradictory. Since both blocks are within ONE sheet, figure_a/b must be None.
    a_rows, b_rows = _matrix_block(_BASE, _SHIFTS)
    gs = _grid_sheets_two_blocks(a_rows, b_rows)
    f = [x for x in detect_within_sheet_fraction_reuse(gs) if x["kind"] == "within_table_fraction_reuse"]
    assert f
    x = f[0]
    assert x["figure_a"] is None and x["figure_b"] is None
    assert x["same_figure"] is False
    assert x["sheet_a"] == x["sheet_b"]


def test_b3_no_flag_when_high_baseline_differences_are_not_integers():
    fractions = [0.12345, 0.23456, 0.34567, 0.45678, 0.56789]
    offsets = [1.25, 2.25, 3.25]
    a = [[1_000_000_000 + r * 10 + c + fractions[(r + c) % len(fractions)]
          for c in range(5)] for r in range(5)]
    b = [[v + offsets[(r + c) % len(offsets)] for c, v in enumerate(row)]
         for r, row in enumerate(a)]
    findings = detect_within_sheet_fraction_reuse(
        _grid_sheets_two_blocks(a, b)
    )
    assert not any(f["kind"] == "within_table_fraction_reuse"
                   for f in findings)


def test_b3_no_flag_on_quarter_offsets_at_large_fraction_resolving_baseline():
    fractions = [0.125, 0.234375, 0.34375, 0.453125, 0.5625]
    offsets = [1.25, 2.25, 3.25]
    a = [[100_000_000_000_000 + r * 10 + c
          + fractions[(r + c) % len(fractions)]
          for c in range(5)] for r in range(5)]
    b = [[v + offsets[(r + c) % len(offsets)] for c, v in enumerate(row)]
         for r, row in enumerate(a)]

    findings = detect_within_sheet_fraction_reuse(
        _grid_sheets_two_blocks(a, b)
    )

    assert not any(f["kind"] == "within_table_fraction_reuse"
                   for f in findings)
