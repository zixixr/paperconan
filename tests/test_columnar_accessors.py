import numpy as np
from paperconan._sheet import Sheet
from paperconan._audit import col_array, find_numeric_blocks

def test_col_array_parity():
    rows = [["h1", "h2"], [1, 10], [2, 20], [3, 30]]
    s = Sheet.from_rows(rows)
    a = col_array(s, 1, 4, 0)
    assert np.allclose(a, [1.0, 2.0, 3.0])
    b = col_array(s, 1, 4, 1)
    assert np.allclose(b, [10.0, 20.0, 30.0])

def test_col_array_nan_for_text():
    s = Sheet.from_rows([["x"], [1], ["y"], [3]])
    a = col_array(s, 0, 4, 0)
    assert np.isnan(a[0]) and a[1] == 1.0 and np.isnan(a[2]) and a[3] == 3.0

def test_find_numeric_blocks_parity():
    rows = [["A", "B", "C"]] + [[i, i * 2, "note"] for i in range(1, 8)]
    s = Sheet.from_rows(rows)
    blocks = find_numeric_blocks(s)
    assert any(r1 - r0 >= 3 and c1 - c0 >= 1 for (r0, r1, c0, c1) in blocks)
    assert all(0 <= c0 < c1 <= s.ncols for (_, _, c0, c1) in blocks)

def test_header_for_uses_text():
    from paperconan._audit import header_for
    s = Sheet.from_rows([["Mass", "Width"], [1.0, 2.0], [3.0, 4.0]])
    assert header_for(s, 1, 0, 2) == ["Mass", "Width"]

def test_block_evidence_int_fidelity():
    from paperconan._audit import _block_evidence
    s = Sheet.from_rows([["H"], [5], [2.5]])
    ev = _block_evidence(s, 1, 3, 0, 1, ["H"], [0])
    flat = [row["values"][0] for row in ev["rows"]]
    assert 5 in flat and isinstance([v for v in flat if v == 5][0], int)
    assert 2.5 in flat

def test_grid_from_rows_only_decimals():
    from paperconan._audit import _grid_from_rows
    s = Sheet.from_rows([[1, 2.345], [3, 7]])
    g = _grid_from_rows(s)
    assert (0, 1) in g and g[(0, 1)] == round(2.345, 9)
    assert (0, 0) not in g and (1, 1) not in g
