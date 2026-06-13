"""Cell-count guard (OOM fix) + wide-block O(col^2) skip (disk/compute fix)."""
import csv

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
