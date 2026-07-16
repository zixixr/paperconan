"""Cross-block / cross-sheet scaled-row reuse.

`detect_row_relations` only compares rows WITHIN one block. When a condition row in
one block (e.g. a DMSO cohort) is an exact scalar multiple of a row in ANOTHER block
of the same sheet (e.g. the MMS cohort) — the Extended Data Fig. 5B pattern — no
within-block detector sees it. `detect_scaled_row_reuse` compares data-rows across
distinct row-bands and sheets. Signal, not verdict — neutral language.
"""
from __future__ import annotations

from paperconan import scan_dir
from paperconan._audit import _attach_benign, benign_reason, detect_scaled_row_reuse
from paperconan._sheet import Sheet
from paperconan.packet import distill_findings_for_review


def _distinct_highprec(n, a, b):
    return [13.0 + ((k * a + b) % 97) + (k + 1) * 0.6180339887 for k in range(n)]


def _two_band_sheet(band1_reused, band2_row, ncols):
    blank = [None] * (ncols + 1)
    return Sheet.from_rows([
        ["DMSO", *([None] * ncols)],
        ["shControl", *_distinct_highprec(ncols, 53, 17)],
        ["shUSP15-2+pPARP1", *band1_reused],
        ["shPARP1-2", *_distinct_highprec(ncols, 71, 41)],
        blank, blank,
        ["MMS", *([None] * ncols)],
        ["shControl", *_distinct_highprec(ncols, 91, 13)],
        ["shUSP15-2+pPARP1", *band2_row],
        ["shPARP1-2+pUSP15", *_distinct_highprec(ncols, 101, 29)],
    ])


def test_detects_scaled_row_across_blocks_in_same_sheet():
    base = _distinct_highprec(60, 31, 7)
    sheet = _two_band_sheet(base, [v * 1.05 for v in base], 60)
    grid_sheets = {("MOESM26.xlsx", "Extended Data Fig. 5B"): sheet}

    findings = detect_scaled_row_reuse(grid_sheets)

    scaled = [f for f in findings if f["kind"] == "scaled_row_reuse"]
    assert len(scaled) == 1, f"expected one scaled-row finding, got {findings}"
    f = scaled[0]
    assert f["same_file"] is True
    assert f["sheet_a"] == f["sheet_b"] == "Extended Data Fig. 5B"
    assert abs(f["ratio"] - 1.05) < 1e-6 or abs(f["ratio"] - 1 / 1.05) < 1e-6
    assert f["run_length"] == 60
    assert f["severity"] == "high"
    # both endpoints are the same condition in the two cohorts
    assert "shUSP15-2+pPARP1" in (f["row_a"], f["row_b"])


def test_detects_identical_row_across_blocks_in_same_sheet():
    # ratio == 1 special case: the SAME data group reappears under a different cohort.
    vals = _distinct_highprec(60, 31, 7)
    sheet = _two_band_sheet(vals, list(vals), 60)
    findings = detect_scaled_row_reuse({("MOESM22.xlsx", "Extended Data Fig. 3G"): sheet})

    ident = [f for f in findings if f["kind"] == "identical_row_reuse"]
    assert len(ident) == 1, f"expected one identical-row finding, got {findings}"
    assert ident[0]["run_length"] == 60
    assert ident[0]["same_file"] is True
    # the same pair must NOT also be reported as a (ratio) scaled row
    pair = {ident[0]["row_a"], ident[0]["row_b"]}
    assert not [f for f in findings
                if f["kind"] == "scaled_row_reuse" and {f["row_a"], f["row_b"]} == pair]


def test_identical_row_across_two_sheets():
    vals = _distinct_highprec(40, 31, 7)
    sa = Sheet.from_rows([["c", *[f"m{i}" for i in range(40)]],
                          ["ctrl", *_distinct_highprec(40, 53, 17)], ["x", *vals]])
    sb = Sheet.from_rows([["c", *[f"m{i}" for i in range(40)]],
                          ["ctrl", *_distinct_highprec(40, 71, 41)], ["y", *list(vals)]])
    findings = detect_scaled_row_reuse({("A.xlsx", "Fig. 1"): sa, ("B.xlsx", "Fig. 2"): sb})
    ident = [f for f in findings if f["kind"] == "identical_row_reuse"]
    assert len(ident) == 1
    assert ident[0]["same_file"] is False


def test_no_false_positive_on_independent_bands():
    sheet = _two_band_sheet(_distinct_highprec(60, 31, 7),
                            _distinct_highprec(60, 200, 3), 60)
    assert detect_scaled_row_reuse({("f.xlsx", "S1"): sheet}) == []


def test_detects_scaled_row_across_two_sheets():
    base = _distinct_highprec(40, 31, 7)
    sa = Sheet.from_rows([
        ["cond", *[f"m{i}" for i in range(40)]],
        ["Control", *_distinct_highprec(40, 53, 17)],
        ["treated", *base],
    ])
    sb = Sheet.from_rows([
        ["cond", *[f"m{i}" for i in range(40)]],
        ["Control", *_distinct_highprec(40, 71, 41)],
        ["treated", *[v * 1.42 for v in base]],
    ])
    findings = detect_scaled_row_reuse({("A.xlsx", "Fig. 1"): sa, ("B.xlsx", "Fig. 2"): sb})

    scaled = [f for f in findings if f["kind"] == "scaled_row_reuse"]
    assert len(scaled) == 1
    assert scaled[0]["same_file"] is False
    assert {scaled[0]["sheet_a"], scaled[0]["sheet_b"]} == {"Fig. 1", "Fig. 2"}


def test_shared_control_across_panels_same_figure_is_benign_but_different_label_is_not():
    vals = _distinct_highprec(40, 31, 7)
    # same-named control row reused across two PANELS of one figure (5A, 5B → main:5)
    sa = Sheet.from_rows([["c", *[f"m{i}" for i in range(40)]],
                          ["shControl", *vals], ["x", *_distinct_highprec(40, 53, 17)]])
    sb = Sheet.from_rows([["c", *[f"m{i}" for i in range(40)]],
                          ["shControl", *list(vals)], ["y", *_distinct_highprec(40, 71, 41)]])
    findings = _attach_benign(detect_scaled_row_reuse(
        {("M.xlsx", "Extended Data Fig. 5A"): sa, ("M.xlsx", "Extended Data Fig. 5B"): sb}))
    ident = [f for f in findings if f["kind"] == "identical_row_reuse"]
    assert len(ident) == 1 and ident[0]["same_figure"] is True and ident[0]["same_sheet"] is False
    assert ident[0].get("likely_benign"), "same-figure shared control should carry a benign note"

    # DIFFERENT labels across panels of one figure is NOT a shared control → no benign note
    sc = Sheet.from_rows([["c", *[f"m{i}" for i in range(40)]],
                          ["Control", *vals], ["z", *_distinct_highprec(40, 53, 17)]])
    sd = Sheet.from_rows([["c", *[f"m{i}" for i in range(40)]],
                          ["USP15 KO", *list(vals)], ["w", *_distinct_highprec(40, 71, 41)]])
    f2 = [f for f in _attach_benign(detect_scaled_row_reuse(
              {("M.xlsx", "Fig. 4j"): sc, ("M.xlsx", "Fig. 4k"): sd}))
          if f["kind"] == "identical_row_reuse"]
    assert len(f2) == 1 and f2[0]["same_figure"] is True
    assert not f2[0].get("likely_benign"), "different-label identical rows are not a shared control"


def test_power_of_ten_scaled_row_is_flagged_likely_benign():
    f = {"kind": "scaled_row_reuse", "ratio": 100.0}
    assert benign_reason(f)
    f2 = {"kind": "scaled_row_reuse", "ratio": 1.05}
    assert not benign_reason(f2)


def test_scan_dir_surfaces_scaled_row_reuse(tmp_path):
    base = _distinct_highprec(60, 31, 7)
    sheet_rows = [
        ["DMSO", *([""] * 60)],
        ["shControl", *_distinct_highprec(60, 53, 17)],
        ["shUSP15-2+pPARP1", *base],
        ["", *([""] * 60)],
        ["", *([""] * 60)],
        ["MMS", *([""] * 60)],
        ["shControl", *_distinct_highprec(60, 91, 13)],
        ["shUSP15-2+pPARP1", *[v * 1.05 for v in base]],
    ]
    data = tmp_path / "data"
    data.mkdir()
    (data / "s.csv").write_text(
        "\n".join(",".join("" if v == "" else str(v) for v in row) for row in sheet_rows) + "\n",
        encoding="utf-8",
    )

    res = scan_dir(str(data), str(tmp_path / "out"), write_html=True)

    scaled = [f for f in res.get("cross_sheet_findings", []) or []
              if f.get("kind") == "scaled_row_reuse"]
    assert scaled, "scan_dir did not surface the cross-block scaled row"
    html = (tmp_path / "out" / "report.html").read_text(encoding="utf-8")
    assert "scaled_row_reuse" in html
