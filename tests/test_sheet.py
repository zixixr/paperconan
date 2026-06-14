import numpy as np
from paperconan._sheet import Sheet

def test_from_rows_roundtrip_types():
    rows = [["label", 1, 2.5, None],
            ["x", 3, 4.0, "txt"],
            [None, True, 0.001, 7]]
    s = Sheet.from_rows(rows)
    assert s.nrows == 3 and s.ncols == 4
    assert s.cell(0, 1) == 1 and isinstance(s.cell(0, 1), int)
    assert s.cell(0, 2) == 2.5 and isinstance(s.cell(0, 2), float)
    assert s.cell(1, 2) == 4.0 and isinstance(s.cell(1, 2), float)
    assert s.cell(0, 0) == "label"
    assert s.cell(0, 3) is None
    assert s.cell(1, 3) == "txt"
    assert s.cell(2, 1) is True

def test_numeric_array_nan_for_nonnumeric():
    s = Sheet.from_rows([["a", 1], [2, None], [3.5, "b"]])
    nm = s.numeric
    assert np.isnan(nm[0, 0]) and nm[0, 1] == 1.0
    assert nm[1, 0] == 2.0 and np.isnan(nm[1, 1])
    assert nm[2, 0] == 3.5 and np.isnan(nm[2, 1])

def test_block_and_numeric_values():
    s = Sheet.from_rows([[1, 2], [3, 4], [5, 6]])
    blk = s.block(0, 3, 0, 2)
    assert blk.shape == (3, 2) and blk[2, 1] == 6.0
    vals = sorted(s.numeric_values())
    assert vals == [1.0, 2.0, 3.0, 4.0, 5.0, 6.0]

def test_ragged_rows_padded():
    s = Sheet.from_rows([[1], [2, 3, 4], [5, 6]])
    assert s.ncols == 3
    assert np.isnan(s.numeric[0, 1]) and s.cell(0, 1) is None

def test_empty_sheet():
    s = Sheet.from_rows([])
    assert s.nrows == 0 and s.ncols == 0
    assert s.numeric.shape == (0, 0)
    assert s.numeric_values() == []
    assert s.cell(0, 0) is None

def test_int_zero_keeps_int_fidelity():
    # 0 is falsy but its (r, c) is still in _ints, so cell() must return int 0.
    s = Sheet.from_rows([[0, 0.0]])
    assert s.cell(0, 0) == 0 and isinstance(s.cell(0, 0), int)
    assert s.cell(0, 1) == 0.0 and not isinstance(s.cell(0, 1), int)
