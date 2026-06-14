import numpy as np
import openpyxl
import pytest
from paperconan._audit import load_workbook_rows
from paperconan._sheet import Sheet


def _write_xlsx(path, rows, sheet_name="S1"):
    wb = openpyxl.Workbook(); ws = wb.active; ws.title = sheet_name
    for row in rows:
        ws.append(row)
    wb.save(path)


def test_load_returns_sheets(tmp_path):
    p = tmp_path / "a.xlsx"
    _write_xlsx(p, [["H1", "H2"], [1, 2.5], [3, 4.0]])
    out = load_workbook_rows(str(p))
    s = out["S1"]
    assert isinstance(s, Sheet)
    assert s.cell(0, 0) == "H1"
    assert s.cell(1, 0) == 1 and isinstance(s.cell(1, 0), int)
    assert s.cell(1, 1) == 2.5
    assert s.nrows == 3 and s.ncols == 2


def test_oversized_sheet_is_none(tmp_path, monkeypatch):
    import paperconan._audit as A
    monkeypatch.setattr(A, "_MAX_CELLS", 5)
    p = tmp_path / "big.xlsx"
    _write_xlsx(p, [[i, i, i] for i in range(10)])   # 30 cells > 5
    out = load_workbook_rows(str(p))
    assert out["S1"] is None


def test_streaming_matches_from_rows(tmp_path):
    """The streamed Sheet must be byte-identical to Sheet.from_rows of the same
    data (numeric NaN-aware, text, ints, dims) — the parity guard for the rewrite."""
    rows = [["h", "k", "note"],
            [1, 2.5, "a"],
            [3, 4.0, None],
            [None, 0.001, "z"],
            [7, 8, "x"]]
    p = tmp_path / "p.xlsx"
    _write_xlsx(p, rows)
    streamed = load_workbook_rows(str(p))["S1"]
    # Build the reference the way scan_dir used to: read via openpyxl into rows, from_rows.
    wb = openpyxl.load_workbook(str(p), data_only=True, read_only=True)
    raw = [list(r) for r in wb["S1"].iter_rows(values_only=True)]
    wb.close()
    ref = Sheet.from_rows(raw)
    assert streamed.nrows == ref.nrows and streamed.ncols == ref.ncols
    assert np.array_equal(np.nan_to_num(streamed.numeric, nan=-123456.5),
                          np.nan_to_num(ref.numeric, nan=-123456.5))
    assert streamed._text == ref._text
    assert streamed._ints == ref._ints


def test_load_csv_returns_sheet(tmp_path):
    from paperconan._audit import load_csv_rows
    p = tmp_path / "d.csv"; p.write_text("a,b\n1,2.5\n3,x\n")
    s = load_csv_rows(str(p), delimiter=",")["d"]
    assert isinstance(s, Sheet)
    assert s.cell(0, 0) == "a"
    assert s.cell(1, 0) == 1 and isinstance(s.cell(1, 0), int)
    assert s.cell(1, 1) == 2.5
    assert s.cell(2, 1) == "x"


def test_load_table_yields_sheets(tmp_path):
    from paperconan._audit import load_table
    p = tmp_path / "d.csv"; p.write_text("x\n1\n2\n3\n")
    out = load_table(str(p))
    assert all(v is None or isinstance(v, Sheet) for v in out.values())


def test_calamine_matches_openpyxl(tmp_path):
    import importlib.util, paperconan._audit as A
    if importlib.util.find_spec("python_calamine") is None:
        import pytest; pytest.skip("python-calamine not installed")
    p = tmp_path / "a.xlsx"
    _write_xlsx(p, [["H", "K"], [1, 2.5], [3, 4.0], [5, 6.25], [None, 7]])
    via_cal = A._load_workbook_calamine(str(p))
    via_op = A._load_workbook_openpyxl(str(p))
    for name in via_op:
        a, b = via_cal[name], via_op[name]
        assert (a is None) == (b is None)
        if a is not None:
            assert a.nrows == b.nrows and a.ncols == b.ncols
            assert np.array_equal(np.nan_to_num(a.numeric, nan=-1.5e9), np.nan_to_num(b.numeric, nan=-1.5e9))
            assert a._text == b._text and a._ints == b._ints
