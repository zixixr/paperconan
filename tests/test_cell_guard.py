"""Cell-count guard (OOM fix) + wide-block O(col^2) skip (disk/compute fix)."""
import csv

import numpy as np
import openpyxl

import paperconan._audit as A


def _make_xlsx(path, nrows, ncols):
    wb = openpyxl.Workbook()
    ws = wb.active
    for i in range(nrows):
        ws.append([float(i * ncols + j) + 0.123 for j in range(ncols)])
    wb.save(path)


def test_oversized_sheet_skipped_and_recorded(tmp_path, monkeypatch):
    monkeypatch.setattr(A, "_MAX_CELLS", 50)            # tiny cap
    _make_xlsx(str(tmp_path / "big.xlsx"), 20, 10)      # 200 cells > 50
    scan = A.scan_dir(str(tmp_path), str(tmp_path / "out"), write_html=False)
    assert any("oversized sheet" in e.get("error", "") for e in scan["scan_errors"])
    assert any(s.get("oversized") for s in scan["scan_stats"]["sheets"])
    assert scan["n_blocks_with_findings"] == 0          # the skipped sheet produced nothing
    assert any(
        item["reason"] == "cell_limit"
        for item in scan["coverage"]["limitations"]
    )


def test_normal_sheet_under_cap_is_audited(tmp_path, monkeypatch):
    monkeypatch.setattr(A, "_MAX_CELLS", 2_000_000)
    _make_xlsx(str(tmp_path / "ok.xlsx"), 10, 6)         # 60 cells, fine
    scan = A.scan_dir(str(tmp_path), str(tmp_path / "out"), write_html=False)
    assert not any(s.get("oversized") for s in scan["scan_stats"]["sheets"])


def test_wide_block_skips_oncol2_detectors(tmp_path, monkeypatch):
    monkeypatch.setattr(A, "_MAX_BLOCK_COLS", 5)         # blocks wider than 5 cols skip relations
    monkeypatch.setattr(A, "_MAX_CELLS", 10_000_000)
    p = tmp_path / "wide.csv"
    with open(p, "w", newline="") as fh:
        w = csv.writer(fh)
        for i in range(8):
            row = [float(i) + 0.11 * j for j in range(12)]   # 12-col block (> 5)
            row[1] = row[0]                                   # col1 == col0 (identical_column bait)
            w.writerow(row)
    scan = A.scan_dir(str(tmp_path), str(tmp_path / "out2"), write_html=False)
    rels = [r for b in scan["relations_blocks"] for r in b.get("relations", [])]
    assert rels == []   # the O(col^2) relation detector was skipped on the wide block


def _guard_stack_targets(monkeypatch, remaining):
    original_full = A.np.full
    original_empty = A.np.empty
    original_vstack = A.np.vstack
    original_hstack = A.np.hstack

    def guarded_full(shape, *args, **kwargs):
        assert np.prod(shape) <= remaining
        return original_full(shape, *args, **kwargs)

    def guarded_empty(shape, *args, **kwargs):
        assert np.prod(shape) <= remaining
        return original_empty(shape, *args, **kwargs)

    def guarded_vstack(arrays, *args, **kwargs):
        rows = sum(array.shape[0] for array in arrays)
        cols = max((array.shape[1] for array in arrays), default=0)
        assert rows * cols <= remaining
        return original_vstack(arrays, *args, **kwargs)

    def guarded_hstack(arrays, *args, **kwargs):
        rows = max((array.shape[0] for array in arrays), default=0)
        cols = sum(array.shape[1] for array in arrays)
        assert rows * cols <= remaining
        return original_hstack(arrays, *args, **kwargs)

    monkeypatch.setattr(A.np, "full", guarded_full)
    monkeypatch.setattr(A.np, "empty", guarded_empty)
    monkeypatch.setattr(A.np, "vstack", guarded_vstack)
    monkeypatch.setattr(A.np, "hstack", guarded_hstack)


def _assert_compact_sheet(sheet, cells, shape):
    assert sheet is not None
    assert cells == np.prod(shape)
    assert sheet.numeric.shape == shape
    assert sheet.numeric.size == cells
    assert sheet.numeric.flags.owndata
    assert sheet.numeric.base is None


def test_column_growth_compacts_overdeclared_rows_before_allocation(monkeypatch):
    monkeypatch.setattr(A, "_MAX_CELLS", 10)
    _guard_stack_targets(monkeypatch, remaining=6)

    sheet, cells = A._fill_sheet_from_rows([[1, 2]], mr=6, mc=1, loaded=4)

    _assert_compact_sheet(sheet, cells, (1, 2))
    assert sheet.cell(0, 0) == 1
    assert sheet.cell(0, 1) == 2


def test_row_growth_compacts_overdeclared_columns_before_allocation(monkeypatch):
    monkeypatch.setattr(A, "_MAX_CELLS", 10)
    _guard_stack_targets(monkeypatch, remaining=6)

    sheet, cells = A._fill_sheet_from_rows([[1], [2]], mr=1, mc=6, loaded=4)

    _assert_compact_sheet(sheet, cells, (2, 1))
    assert sheet.cell(0, 0) == 1
    assert sheet.cell(1, 0) == 2


def test_overdeclared_sheet_returns_compact_owning_numeric_array(monkeypatch):
    monkeypatch.setattr(A, "_MAX_CELLS", 10)

    sheet, cells = A._fill_sheet_from_rows([[1, 2]], mr=3, mc=2, loaded=0)

    _assert_compact_sheet(sheet, cells, (1, 2))
