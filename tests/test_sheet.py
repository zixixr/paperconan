import numpy as np
import gc
import weakref
import paperconan._sheet as sheet_module
from paperconan._sheet import Sheet
from paperconan._sheet import SheetBuilder

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

def test_cell_returns_builtin_not_numpy():
    # Evidence cells are JSON-serialized; cell() must hand back built-in
    # int/float, never numpy scalars (np.float64 fails json.dump / drifts under
    # default=str). type() is exact-checked on purpose, not isinstance.
    s = Sheet.from_rows([[2.5, 7]])
    assert type(s.cell(0, 0)) is float
    assert type(s.cell(0, 1)) is int


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


def test_iter_numeric_values_is_row_major_and_preserves_exact_types():
    wide = 2**53 + 1
    sheet = Sheet.from_rows([
        ["label", 1, 2.5],
        [wide, None, 4.0],
    ])

    values = list(sheet.iter_numeric_values())

    assert values == [1, 2.5, wide, 4.0]
    assert type(values[0]) is int
    assert type(values[1]) is float
    assert type(values[2]) is int
    assert sheet.numeric_values() == values


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


def test_wide_adjacent_integers_roundtrip_without_merging():
    left = 2**53
    right = left + 1
    sheet = Sheet.from_rows([[left, right]])
    assert sheet.cell(0, 0) == left
    assert sheet.cell(0, 1) == right
    assert sheet.cell(0, 0) != sheet.cell(0, 1)
    assert left in sheet.numeric_values()
    assert right in sheet.numeric_values()


def test_wide_integer_cells_remain_numeric_in_mask():
    sheet = Sheet.from_rows([[2**53], [2**53 + 1], [2**53 + 2]])
    assert sheet.numeric_mask()[:, 0].tolist() == [True, True, True]


def test_integer_dense_sheet_uses_geometry_bounded_type_mask():
    sheet = Sheet.from_rows(
        ([row * 100 + col for col in range(100)] for row in range(100))
    )

    assert isinstance(sheet._ints, np.ndarray)
    assert sheet._ints.dtype == np.bool_
    assert sheet._ints.shape == sheet.numeric.shape == (100, 100)
    assert int(np.count_nonzero(sheet._ints)) == 10_000


def test_from_rows_does_not_retain_consumed_iterator_rows():
    class WeakRow(list):
        pass

    prior = []

    def rows():
        for row_number in range(4):
            if prior:
                gc.collect()
                assert prior[-1]() is None
            row = WeakRow([row_number, float(row_number)])
            prior.append(weakref.ref(row))
            yield row
            del row

    sheet = Sheet.from_rows(rows())

    assert sheet.nrows == 4
    assert sheet.cell(3, 0) == 3


def test_streaming_builder_grows_dense_capacity_geometrically(
    monkeypatch,
):
    resize_calls = []
    original_resize = sheet_module._resize_in_place

    def tracked_resize(*args, **kwargs):
        resize_calls.append((args[1], args[2]))
        return original_resize(*args, **kwargs)

    monkeypatch.setattr(
        sheet_module, "_resize_in_place", tracked_resize
    )
    builder = SheetBuilder(max_cells=1_024)

    for row in range(1_024):
        builder.append_row([row])
    sheet = builder.finish()

    assert sheet.numeric.shape == (1_024, 1)
    assert len(resize_calls) < 40
    assert max(cols for _rows, cols in resize_calls) == 1
