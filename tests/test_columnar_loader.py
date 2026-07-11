import numpy as np
import openpyxl
import pytest
from zipfile import ZIP_DEFLATED, ZipFile
from paperconan._audit import load_workbook_rows
from paperconan._sheet import Sheet


def _write_xlsx(path, rows, sheet_name="S1"):
    wb = openpyxl.Workbook(); ws = wb.active; ws.title = sheet_name
    for row in rows:
        ws.append(row)
    wb.save(path)


def _write_xlsx_with_exact_adjacent_wide_integers(path):
    _write_xlsx(path, [["a", "b"], [2**53, 2**53]])
    with ZipFile(path) as src:
        members = [(info, src.read(info.filename)) for info in src.infolist()]
    old = b'<c r="B2" t="n"><v>9007199254740992</v></c>'
    new = b'<c r="B2" t="n"><v>9007199254740993</v></c>'
    with ZipFile(path, "w", ZIP_DEFLATED) as dst:
        for info, data in members:
            if info.filename == "xl/worksheets/sheet1.xml":
                assert old in data
                data = data.replace(old, new, 1)
            dst.writestr(info, data)


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


def test_calamine_huge_bounding_box_is_none_not_oom(tmp_path, monkeypatch):
    import importlib.util
    if importlib.util.find_spec("python_calamine") is None:
        pytest.skip("no calamine")
    import paperconan._audit as A
    monkeypatch.setattr(A, "_MAX_CELLS", 1_000_000)
    p = tmp_path / "huge.xlsx"; wb = openpyxl.Workbook(); ws = wb.active
    ws["A1"] = 1
    ws["C1000000"] = 2          # ~3M declared cells > 1M cap (within Excel's row limit)
    wb.save(str(p))

    # The oversized sheet must be rejected from its DECLARED dimensions, BEFORE
    # to_python materializes the full bounding box (that materialization is what
    # OOMs in prod). Spy on to_python to prove it is never called on this sheet.
    import python_calamine as pc
    orig = pc.CalamineSheet.to_python
    calls = {"n": 0}

    def spy(self, *a, **k):
        calls["n"] += 1
        return orig(self, *a, **k)

    monkeypatch.setattr(pc.CalamineSheet, "to_python", spy)
    out = A._load_workbook_calamine(str(p))   # must NOT OOM; oversized -> None
    assert out["Sheet"] is None
    assert calls["n"] == 0                     # never materialized the huge box


def test_streaming_loader_preserves_adjacent_wide_integers(tmp_path):
    import paperconan._audit as audit

    p = tmp_path / "wide.xlsx"
    _write_xlsx_with_exact_adjacent_wide_integers(p)
    sheet = audit._load_workbook_openpyxl(str(p))["S1"]
    assert sheet.cell(1, 0) == 2**53
    assert sheet.cell(1, 1) == 2**53 + 1
    assert sheet.cell(1, 0) != sheet.cell(1, 1)


def test_default_loader_falls_back_to_openpyxl_for_wide_ooxml_integers(
    tmp_path, monkeypatch
):
    import paperconan._audit as audit

    p = tmp_path / "wide.xlsx"
    _write_xlsx_with_exact_adjacent_wide_integers(p)
    openpyxl_load = audit._load_workbook_openpyxl
    calls = []

    def spy(path):
        calls.append(path)
        return openpyxl_load(path)

    monkeypatch.setattr(audit, "_load_workbook_openpyxl", spy)

    sheet = audit.load_workbook_rows(str(p))["S1"]

    assert calls == [str(p)]
    assert sheet.cell(1, 0) == 2**53
    assert sheet.cell(1, 1) == 2**53 + 1
    assert sheet.cell(1, 0) != sheet.cell(1, 1)


@pytest.mark.parametrize("suffix", [".xls", ".xlsb"])
def test_legacy_workbooks_do_not_use_wide_integer_openpyxl_fallback(
    tmp_path, monkeypatch, suffix
):
    import paperconan._audit as audit
    import python_calamine

    class FakeSheet:
        height = 1
        width = 1

        def to_python(self, skip_empty_area=False):
            return [[float(2**53)]]

    class FakeWorkbook:
        sheet_names = ["S1"]

        def get_sheet_by_name(self, name):
            assert name == "S1"
            return FakeSheet()

    class FakeCalamineWorkbook:
        @staticmethod
        def from_path(path):
            return FakeWorkbook()

    def forbidden(path):
        raise AssertionError("legacy workbook must stay on the Calamine path")

    monkeypatch.setattr(python_calamine, "CalamineWorkbook", FakeCalamineWorkbook)
    monkeypatch.setattr(audit, "_load_workbook_openpyxl", forbidden)

    sheet = audit.load_workbook_rows(str(tmp_path / f"wide{suffix}"))["S1"]

    assert sheet.cell(0, 0) == 2**53
