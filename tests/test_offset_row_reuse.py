"""Constant-offset row reuse — the row twin of `constant_offset` (B = A + c).

`detect_short_row_reuse` already finds identical (B = A) and scaled (B = k·A) runs between
two rows, mirroring `identical_column` and `constant_ratio`. The linear family had no
row-oriented OFFSET member, though columns have had `constant_offset` all along: a row
reused at another cohort with a constant added (a baseline shift / additive copy, e.g.
B = A + 0.589) was invisible. This completes the family. Signal, not verdict.
"""
from __future__ import annotations

from paperconan import scan_dir
from paperconan._audit import detect_short_row_reuse
from paperconan._sheet import Sheet

RA = [10.316768, 22.849559, 34.647899, 15.173653, 28.508241, 19.930517]


def _two_panel(rb, ncols=6):
    return Sheet.from_rows([
        ["panel1", *([None] * ncols)],
        ["cond", *RA],
        ["panel2", *([None] * ncols)],
        ["cond", *rb],
    ])


def test_detects_constant_offset_between_panels():
    sheet = _two_panel([v + 0.589 for v in RA])
    findings = detect_short_row_reuse({("f.xlsx", "S1"): sheet})
    off = [f for f in findings if f["kind"] == "offset_row_reuse"]
    assert len(off) == 1, f"expected one offset-row finding, got {findings}"
    assert abs(off[0]["offset"] - 0.589) < 1e-4 or abs(off[0]["offset"] + 0.589) < 1e-4
    assert off[0]["run_length"] >= 3
    assert off[0]["severity"] == "high"


def test_identical_rows_are_identical_not_offset():
    # c == 0 is the identical case, not an offset.
    sheet = _two_panel(list(RA))
    findings = detect_short_row_reuse({("f.xlsx", "S2"): sheet})
    assert not [f for f in findings if f["kind"] == "offset_row_reuse"]
    assert [f for f in findings if f["kind"] == "identical_row_reuse"]


def test_no_false_positive_on_independent_rows():
    sheet = _two_panel([13.221417, 41.55238, 7.918263, 29.104772, 33.870915, 2.446181])
    assert detect_short_row_reuse({("f.xlsx", "S3"): sheet}) == []


def test_same_band_offset_suppressed_as_curve_step():
    # Two ADJACENT rows (no header between) differing by a constant is a smooth-curve step,
    # not a copied panel — suppressed like the scaled same-band case.
    sheet = Sheet.from_rows([["a", *RA], ["b", *[v + 0.589 for v in RA]]])
    assert not [f for f in detect_short_row_reuse({("f.xlsx", "S4"): sheet})
                if f["kind"] == "offset_row_reuse"]


def test_no_false_positive_on_near_zero_rows():
    # Two rows of tiny (~1e-4) high-precision values: a fixed-floor tolerance made unrelated
    # near-zero rows read as a constant difference larger than the values (JCI182394 Fig.S7).
    a = [0.000549444, 0.000210289, 0.000277287, 0.000418122, 0.000133905, 0.000391044]
    b = [0.001883333, 0.001546122, 0.001611240, 0.001101765, 0.002049881, 0.000904611]
    sheet = _two_panel(b)  # RA replaced below
    sheet = Sheet.from_rows([["panel1", *([None] * 6)], ["cond", *a],
                             ["panel2", *([None] * 6)], ["cond", *b]])
    assert not [f for f in detect_short_row_reuse({("f.xlsx", "S5"): sheet})
                if f["kind"] == "offset_row_reuse"]


def test_offset_run_not_truncated_by_large_tail_cell():
    # A small constant offset over cells that include a LARGE-magnitude cell at the run tail:
    # the non-triviality check must be anchored to the run, not the current cell, or the run
    # is truncated below the 3-column minimum and the finding is dropped (review #1).
    a = [10.316768, 22.849559, 1000.647899]
    b = [v + 0.05 for v in a]
    sheet = Sheet.from_rows([["panel1", None, None, None], ["cond", *a],
                             ["panel2", None, None, None], ["cond", *b]])
    off = [f for f in detect_short_row_reuse({("f.xlsx", "S6"): sheet})
           if f["kind"] == "offset_row_reuse"]
    assert len(off) == 1 and off[0]["run_length"] == 3, f"large-tail run truncated: {off}"


def test_offset_row_reuse_is_derived_relation_symmetric_with_scaled():
    # An additive constant is a common benign derivation (baseline subtraction) — offset must
    # be demotable like its scaled twin, with the same same-label guard (review #2).
    from paperconan._profiles import _is_derived_relation
    assert _is_derived_relation(
        {"kind": "offset_row_reuse", "row_a": "conc (ng/mL)", "row_b": "OD (a.u.)"})
    assert not _is_derived_relation(
        {"kind": "offset_row_reuse", "row_a": "TNF (pg/mL)", "row_b": "TNF (pg/mL)"})


def test_scan_dir_surfaces_offset_row_reuse(tmp_path):
    rows = [
        ["panel1", "", "", "", "", "", ""],
        ["cond", *RA],
        ["panel2", "", "", "", "", "", ""],
        ["cond", *[v + 0.589 for v in RA]],
    ]
    data = tmp_path / "data"
    data.mkdir()
    (data / "s.csv").write_text(
        "\n".join(",".join("" if x == "" else str(x) for x in r) for r in rows) + "\n",
        encoding="utf-8")
    res = scan_dir(str(data), str(tmp_path / "out"), write_html=False)
    assert [f for f in res.get("cross_sheet_findings", []) or []
            if f.get("kind") == "offset_row_reuse"]
