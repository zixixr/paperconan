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


# ---------- Issue 2: near-duplicate delta characterization ----------

def test_delta_perfect_dup():
    """Two identical tables — a clean re-plot. Pattern must be perfect_dup."""
    ga, gb = _identical_grids()
    cf = detect_collisions({("a.xlsx", "Sheet1"): ga, ("a.xlsx", "Sheet2"): gb})[0]
    delta = cf["delta"]
    assert delta["pattern"] == "perfect_dup"
    assert delta["only_in_a"] == 0 and delta["only_in_b"] == 0
    assert delta["modified_cells"] == 0


def test_delta_superset_extra_column():
    """B = A plus one extra replicate column (new positions, new values), nothing
    altered. This is the benign 'main shows n=5, extended shows n=6' shape —
    pattern superset, modified_cells == 0, extras only on one side."""
    ga, _ = _identical_grids(n_rows=10, n_cols=3)
    gb = dict(ga)
    extra_v = 900.1234
    for r in range(10):  # an extra 4th column present only in B
        gb[(r, 3)] = round(extra_v, 4)
        extra_v += 0.55
    cf = detect_collisions({("a.xlsx", "Sheet1"): ga, ("a.xlsx", "Sheet2"): gb})[0]
    delta = cf["delta"]
    assert delta["pattern"] == "superset"
    assert delta["modified_cells"] == 0
    assert delta["only_in_a"] == 0
    assert delta["only_in_b"] >= 10


def test_delta_value_tweaked():
    """B is a copy of A with a few cells changed in place (same position, new value).
    This is the copy-then-tweak fingerprint — pattern value_tweaked, the most
    forensically interesting, distinct from a clean re-plot."""
    ga, gb = _identical_grids(n_rows=10, n_cols=3)
    gb[(0, 0)] = ga[(0, 0)] + 0.0009
    gb[(5, 2)] = ga[(5, 2)] + 0.0011
    cf = detect_collisions({("a.xlsx", "Sheet1"): ga, ("a.xlsx", "Sheet2"): gb})[0]
    delta = cf["delta"]
    assert delta["modified_cells"] == 2
    assert delta["pattern"] == "value_tweaked"


def test_delta_shifted_layout_is_perfect_dup_not_tweaked():
    """Same numbers stored at a different column offset (a main figure and an
    extended figure laying the cohort out differently). The value multiset is
    identical, so this is a perfect_dup of the data — NOT value_tweaked, even
    though raw (row,col) positions disagree."""
    ga, _ = _identical_grids(n_rows=10, n_cols=3)
    gb = {(r, c + 1): v for (r, c), v in ga.items()}  # shift every cell one column right
    cf = detect_collisions({("M8.xlsx", "Figure 5o"): ga,
                            ("M8.xlsx", "Figure 5o2"): gb})[0]
    delta = cf["delta"]
    assert delta["only_in_a"] == 0 and delta["only_in_b"] == 0
    assert delta["pattern"] == "perfect_dup", \
        f"identical value multiset must read as perfect_dup, got {delta['pattern']}"
