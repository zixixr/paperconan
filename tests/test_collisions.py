"""Unit tests for the cross-sheet collision detector (detect_collisions).

These build decimal grids directly (the same shape _grid_from_rows produces)
so we can exercise severity/context logic without round-tripping through xlsx.
"""
from __future__ import annotations

from paperconan._audit import detect_collisions


def _identical_grids(n_rows=10, n_cols=3, base=1.1001):
    """A {(r,c): value} grid with distinct decimal values, returned twice."""
    g = {}
    v = base
    for r in range(n_rows):
        for c in range(n_cols):
            g[(r, c)] = round(v, 4)
            v += 0.7137
    return dict(g), dict(g)


def _find(findings, kind):
    return next((f for f in findings if f["kind"] == kind), None)


# ---------- Issue 1: context-aware severity ----------

def test_same_figure_same_file_overlap_is_downgraded():
    """Two panels of the SAME figure in the SAME file sharing identical data is
    the expected combined-vs-individual re-plot — must be downgraded, not high."""
    ga, gb = _identical_grids()
    grids = {
        ("MOESM16.xlsx", "exFig.6i"): ga,
        ("MOESM16.xlsx", "exFig.6k-n"): gb,
    }
    findings = detect_collisions(grids)
    assert findings, "expected a collision finding"
    cf = findings[0]
    assert cf["same_figure"] is True
    assert cf["figure_a"] == cf["figure_b"]
    assert cf["severity"] == "low", f"same-figure re-plot should be low, got {cf['severity']}"
    assert cf.get("context"), "same-figure finding should carry a benign context note"


def test_cross_figure_cross_file_overlap_keeps_severity():
    """Main Fig 5o vs Extended Fig 6b-e — different figures, different files.
    This is the one worth attention: severity must NOT be downgraded."""
    ga, gb = _identical_grids()
    grids = {
        ("MOESM8.xlsx", "Figure 5o"): ga,
        ("MOESM16.xlsx", "exFig.6b-e"): gb,
    }
    findings = detect_collisions(grids)
    cf = findings[0]
    assert cf["same_figure"] is False
    assert cf["figure_a"] != cf["figure_b"]
    assert cf["severity"] == "high", \
        f"cross-figure position-identical should stay high, got {cf['severity']}"


def test_unparseable_sheet_names_are_not_same_figure():
    """If we can't parse a figure id from the sheet name, never claim same_figure."""
    ga, gb = _identical_grids()
    grids = {
        ("a.xlsx", "Sheet1"): ga,
        ("a.xlsx", "Sheet2"): gb,
    }
    cf = detect_collisions(grids)[0]
    assert cf["same_figure"] is False
    assert cf["severity"] == "high"
