"""Unit tests for the cross-sheet collision detector (detect_collisions).

These build decimal grids directly (the same shape _grid_from_rows produces)
so we can exercise severity/context logic without round-tripping through xlsx.
"""
from __future__ import annotations

from paperconan._audit import (
    Sheet,
    _grid_from_rows,
    build_cross_sheet_summary,
    detect_collisions,
)


def _grid_from_sheet(sheet):
    grid = {}
    for r in range(sheet.nrows):
        for c in range(sheet.ncols):
            v = sheet.cell(r, c)
            if isinstance(v, (int, float)) and not isinstance(v, bool):
                grid[(r, c)] = round(float(v), 9)
    return grid


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


def test_cross_sheet_finding_carries_matched_control_labels():
    rows_a = [
        ["condition", "day", "control", "treated"],
        ["rep1", 0.0, 1.23, 9.11],
        ["rep2", 1.0, 1.45, 9.31],
        ["rep3", 2.0, 1.67, 9.51],
        ["rep4", 3.0, 1.89, 9.71],
        ["rep5", 4.0, 2.01, 9.91],
        ["rep6", 5.0, 2.23, 10.11],
    ]
    rows_b = [
        ["condition", "day", "vehicle control", "drug B"],
        ["rep1", 0.0, 1.23, 4.11],
        ["rep2", 1.0, 1.45, 4.31],
        ["rep3", 2.0, 1.67, 4.51],
        ["rep4", 3.0, 1.89, 4.71],
        ["rep5", 4.0, 2.01, 4.91],
        ["rep6", 5.0, 2.23, 5.11],
    ]
    sheet_a = Sheet.from_rows(rows_a)
    sheet_b = Sheet.from_rows(rows_b)

    findings = detect_collisions(
        {("a.xlsx", "Fig. 1 control"): _grid_from_sheet(sheet_a),
         ("b.xlsx", "Fig. 2 control"): _grid_from_sheet(sheet_b)},
        sheets={("a.xlsx", "Fig. 1 control"): sheet_a,
                ("b.xlsx", "Fig. 2 control"): sheet_b},
    )

    cf = findings[0]
    assert "control" in cf["label_context_a"]["text"].lower()
    assert "vehicle control" in cf["label_context_b"]["text"].lower()
    assert cf["shared_context"]["shared_control_or_baseline"] is True


def test_cross_sheet_context_marks_time_axis_from_local_labels():
    rows_a = [
        ["sample", "time", "signal"],
        ["r1", 0, 1.1],
        ["r2", 1, 1.3],
        ["r3", 2, 1.5],
        ["r4", 3, 1.7],
        ["r5", 4, 1.9],
        ["r6", 5, 2.1],
    ]
    rows_b = [
        ["sample", "time", "signal"],
        ["r1", 0, 8.1],
        ["r2", 1, 8.3],
        ["r3", 2, 8.5],
        ["r4", 3, 8.7],
        ["r5", 4, 8.9],
        ["r6", 5, 9.1],
    ]
    sheet_a = Sheet.from_rows(rows_a)
    sheet_b = Sheet.from_rows(rows_b)

    cf = detect_collisions(
        {("a.xlsx", "Fig. 1"): _grid_from_sheet(sheet_a),
         ("b.xlsx", "Fig. 2"): _grid_from_sheet(sheet_b)},
        sheets={("a.xlsx", "Fig. 1"): sheet_a,
                ("b.xlsx", "Fig. 2"): sheet_b},
    )[0]

    assert cf["shared_context"]["shared_axis_or_coordinate"] is True
    assert cf["delta"]["pattern"] != "perfect_dup"


def test_compact_label_context_matches_sheet_compatibility_path():
    rows_a = [
        ["condition", "control", "treated"],
        ["rep1", 1.2345, 9.1111],
        ["rep2", 1.4567, 9.3131],
        ["rep3", 1.6789, 9.5151],
        ["rep4", 1.8901, 9.7171],
        ["rep5", 2.0123, 9.9191],
        ["rep6", 2.2345, 10.1111],
    ]
    rows_b = [
        ["condition", "vehicle control", "drug B"],
        ["rep1", 1.2345, 4.1111],
        ["rep2", 1.4567, 4.3131],
        ["rep3", 1.6789, 4.5151],
        ["rep4", 1.8901, 4.7171],
        ["rep5", 2.0123, 4.9191],
        ["rep6", 2.2345, 5.1111],
    ]
    sheets = {
        ("a.xlsx", "Fig. 1 control"): Sheet.from_rows(rows_a),
        ("b.xlsx", "Fig. 2 control"): Sheet.from_rows(rows_b),
    }
    summaries = {
        key: build_cross_sheet_summary(*key, source)[0]
        for key, source in sheets.items()
    }
    grids = {key: _grid_from_rows(source) for key, source in sheets.items()}

    direct = detect_collisions(grids, sheets=sheets)
    compact = detect_collisions(
        {key: summary.grid for key, summary in summaries.items()},
        sheets={key: summary.labels for key, summary in summaries.items()},
    )

    assert compact == direct


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


def test_detects_cross_sheet_decimal_tail_reuse_with_shifted_layout():
    """B copies A's measurement block, shifts it up two rows, and edits only the
    high-order decimal digit. Exact-value overlap misses this, but the long
    fractional tails remain aligned at one table offset.
    """
    ga, gb = {}, {}
    for r in range(12):
        for c in range(3):
            tail = f"{r:02d}{c:02d}731"
            ga[(r + 5, c + 2)] = float(f"0.{(r + c) % 9}{tail}")
            gb[(r + 3, c + 2)] = float(f"0.{((r + c) % 9 + 3) % 10}{tail}")

    findings = detect_collisions({
        ("M.xlsx", "Figure 5d"): ga,
        ("M.xlsx", "Supplementary Figure 6g"): gb,
    })

    tail = _find(findings, "cross_sheet_decimal_tail_reuse")
    assert tail is not None
    assert tail["offset_rows"] == -2
    assert tail["offset_cols"] == 0
    assert tail["tail_match_count"] == 36
    assert tail["severity"] == "high"
    assert tail["examples"][0]["value_a"] != tail["examples"][0]["value_b"]


def test_decimal_tail_reuse_requires_long_tail_cluster_not_short_decimals():
    ga, gb = {}, {}
    for r in range(20):
        ga[(r, 0)] = round(1.1 + r * 0.1, 1)
        gb[(r, 0)] = round(5.1 + r * 0.1, 1)

    findings = detect_collisions({
        ("M.xlsx", "Figure 1"): ga,
        ("M.xlsx", "Figure 2"): gb,
    })

    assert _find(findings, "cross_sheet_decimal_tail_reuse") is None


def test_decimal_tail_reuse_fixed_denominator_is_downgraded_with_reason():
    ga, gb = {}, {}
    shifts = [1, 3, 2, 4, 1, 5, 2, 6, 3, 7, 4, 8]
    for r, shift in enumerate(shifts):
        va = (r + 1) / 7
        ga[(r, 0)] = va
        gb[(r, 0)] = va + shift

    findings = detect_collisions({
        ("M.xlsx", "Figure 2"): ga,
        ("M.xlsx", "Figure 3"): gb,
    }, profile="forensic")

    tail = _find(findings, "cross_sheet_decimal_tail_reuse")
    assert tail is not None
    assert tail["severity"] == "low"
    assert tail["tail_benign_reason"] == "fixed_denominator:1/7"


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


# ---------- Issue 3: shared-axis overlap downgrade ----------
# A cross-figure overlap whose shared (row,col) cells concentrate on a column that
# is an axis (serial-dilution dose ladder, swept time/field axis, or a column reused
# across many sheets) is a shared-x-axis artifact, not cross-experiment reuse. It must
# be downgraded — but only when the rest of the table diverges (pattern != perfect_dup);
# a full-table duplicate stays high.

def test_shared_dose_axis_overlap_is_downgraded():
    """Fig 3e and Fig 5b are two dose-response curves: they share the identical
    serial-dilution (1:3) concentration column at the same positions, but the
    measured values differ. The overlap is the dose axis — must be downgraded."""
    dose = [16.6667, 5.55556, 1.85185, 0.617284, 0.205761, 0.0685871, 0.0228624, 0.00762080]
    ga, gb = {}, {}
    for r, d in enumerate(dose):
        ga[(r, 0)] = round(d, 6); gb[(r, 0)] = round(d, 6)          # shared dose axis
        ga[(r, 1)] = round(10.0 + r * 0.3137, 4); ga[(r, 2)] = round(50.0 - r * 0.71, 4)
        gb[(r, 1)] = round(90.0 - r * 0.41, 4);   gb[(r, 2)] = round(3.0 + r * 0.55, 4)
    cf = detect_collisions({("M.xlsx", "Fig. 3e"): ga, ("M.xlsx", "Fig. 5b"): gb})[0]
    assert cf["kind"] == "cross_sheet_position_identical"
    assert cf["same_figure"] is False
    assert cf["delta"]["pattern"] != "perfect_dup"
    assert cf.get("axis_overlap") is True
    assert cf["severity"] == "low", f"shared dose-axis overlap should be low, got {cf['severity']}"
    assert cf.get("likely_benign")


def test_recurring_axis_column_across_sheets_is_downgraded():
    """A column whose value-set recurs across >=3 sheets is a shared axis even when
    it is not a clean progression. A cross-figure pair sharing only that column
    must be downgraded."""
    axis = [0.1234, 0.8765, 0.4567, 0.9876, 0.3210, 0.6540, 0.2222]  # not a progression
    def mk(base):
        g = {}
        for r, a in enumerate(axis):
            g[(r, 0)] = round(a, 6)
            g[(r, 1)] = round(base + r * 0.137, 4)   # distinct measurement per sheet
        return g
    grids = {("M.xlsx", "Figure 1O"): mk(10.0),
             ("M.xlsx", "sFigure 2D"): mk(40.0),
             ("M.xlsx", "Figure 5D"): mk(70.0)}
    findings = detect_collisions(grids)
    pair = next(f for f in findings
                if {f["sheet_a"], f["sheet_b"]} == {"Figure 1O", "sFigure 2D"})
    assert pair.get("axis_overlap") is True
    assert pair["severity"] == "low"


def test_full_table_dup_not_downgraded_by_axis_rule():
    """A cross-figure overlap where EVERY column matches (perfect_dup) is a full
    duplicate / re-plot — it must stay high regardless of the axis rule."""
    ga, gb = _identical_grids()
    cf = detect_collisions({("M8.xlsx", "Figure 5o"): ga,
                            ("M16.xlsx", "exFig.6b-e"): gb})[0]
    assert cf["delta"]["pattern"] == "perfect_dup"
    assert cf["severity"] == "high"
    assert cf.get("axis_overlap") is not True


def test_copied_measurement_column_keeps_severity():
    """Boundary guard: a pair that shares an axis AND a copied (realistic, non-progression)
    MEASUREMENT column — with a third column divergent — must stay HIGH. The duplicated
    measurement is the forensic signal; only the axis being shared must not buy a downgrade."""
    axis = [16.6667, 5.55556, 1.85185, 0.617284, 0.205761, 0.0685871, 0.0228624, 0.00762080]
    meas = [12.7431, 3.1188, 88.4502, 7.6613, 41.2099, 0.9931, 23.8847, 55.0024]  # not a progression
    ga, gb = {}, {}
    for r in range(len(axis)):
        ga[(r, 0)] = round(axis[r], 6); gb[(r, 0)] = round(axis[r], 6)   # shared axis
        ga[(r, 1)] = meas[r];           gb[(r, 1)] = meas[r]             # COPIED measurement
        ga[(r, 2)] = round(10.0 + r * 0.3137, 4)
        gb[(r, 2)] = round(90.0 - r * 0.41, 4)                          # divergent column
    cf = detect_collisions({("M.xlsx", "Fig. 3e"): ga, ("M.xlsx", "Fig. 5b"): gb})[0]
    assert cf["same_figure"] is False
    assert cf.get("axis_overlap") is not True, "a copied measurement column must not be treated as axis"
    assert cf["severity"] == "high"


# ---------------------------------------------------------------------------
# B1: cross_sheet_column_duplicate — full-column duplication across panels,
# including the integer / 1-decimal columns detect_collisions' >=3dp grids miss.
# ---------------------------------------------------------------------------
from paperconan._audit import detect_cross_sheet_column_duplicates, figure_key  # noqa: E402


def _sheet_from_cols(labels, cols):
    rows = [list(labels)]
    for r in range(len(cols[0])):
        rows.append([cols[j][r] for j in range(len(cols))])
    return Sheet.from_rows(rows)


def _b1_oracle(panels):
    """Independent ground truth. panels: {(file,sheet): {label: values}}. Returns the set of
    frozenset({(file,sheet), (file,sheet)}) pairs that SHOULD be a HIGH duplicate: byte-identical
    high-cardinality non-axis column of len>=12, different figure namespaces, and not the
    all-integer-short case."""
    import numpy as np
    cols = []
    for (f, s), colmap in panels.items():
        for label, vals in colmap.items():
            cols.append((f, s, label, [float(v) for v in vals]))
    def axis_like(a):
        if len(set(round(v, 9) for v in a)) <= 1:
            return True
        d = np.diff(a)
        return bool(np.allclose(d, d[0], atol=1e-9 * max(max(abs(x) for x in a), 1e-300), rtol=1e-9) and abs(d[0]) > 0)
    high = set()
    for i in range(len(cols)):
        for j in range(i + 1, len(cols)):
            fa, sa, _la, a = cols[i]
            fb, sb, _lb, b = cols[j]
            if (fa, sa) == (fb, sb) or len(a) != len(b):
                continue
            if [round(x, 6) for x in a] != [round(x, 6) for x in b]:
                continue
            n = len(a)
            if n < 12 or axis_like(a) or len(set(round(v, 9) for v in a)) < max(6, n // 2):
                continue
            all_int = all(abs(v - round(v)) < 1e-9 for v in a)
            if all_int and (n < 25 or len(set(round(v, 9) for v in a)) < max(12, int(0.7 * n))):
                continue
            fka, fkb = figure_key(sa), figure_key(sb)
            if fka is not None and fka == fkb:
                continue  # same figure -> low, not high
            high.add(frozenset({(fa, sa), (fb, sb)}))
    return high


def _run_b1(panels):
    gs = {k: _sheet_from_cols(list(v.keys()), list(v.values())) for k, v in panels.items()}
    return detect_cross_sheet_column_duplicates(gs)


def test_b1_flags_cross_figure_column_duplicate_and_matches_oracle():
    dup = [3.0, 3.2, 2.5, 2.8, 2.9, 2.2, 5.0, 5.2, 4.5, 4.8, 4.9, 4.2, 6.1, 6.3, 5.7]  # 15, 1-dp
    other = [1.1, 2.4, 3.9, 0.7, 5.5, 4.2, 6.8, 2.1, 3.3, 7.4, 1.9, 8.2, 0.5, 4.7, 6.0]
    panels = {
        ("F3.xls", "Figure 3b"): {"NBS1-K388R": dup, "ctrl": other},
        ("F9.xls", "Extended Data Fig. 9d"): {"Si-LDHA": list(dup), "misc": list(reversed(other))},
    }
    f = _run_b1(panels)
    hi = [x for x in f if x["severity"] == "high"]
    assert len(hi) == 1, f
    assert hi[0]["size_a"] == 15 and hi[0]["same_figure"] is False
    got = {frozenset({(x["file_a"], x["sheet_a"]), (x["file_b"], x["sheet_b"])}) for x in hi}
    assert got == _b1_oracle(panels)


def test_b1_no_flag_on_shared_axis_column():
    axis = [0.5 * (i + 1) for i in range(15)]        # perfect progression → axis
    panels = {
        ("A.xls", "Figure 1a"): {"week": list(axis), "m": [1.1 * i + 0.3 for i in range(15)]},
        ("B.xls", "Figure 2a"): {"week": list(axis), "m": [9.0 - 0.2 * i for i in range(15)]},
    }
    f = _run_b1(panels)
    assert not [x for x in f if x["severity"] == "high"]
    assert _b1_oracle(panels) == set()


def test_b1_same_figure_is_low_not_high():
    dup = [3.0, 3.2, 2.5, 2.8, 2.9, 2.2, 5.0, 5.2, 4.5, 4.8, 4.9, 4.2, 6.1]
    panels = {
        ("M.xls", "Figure 4b"): {"control": list(dup)},
        ("M.xls", "Figure 4c"): {"control": list(dup)},
    }
    f = _run_b1(panels)
    assert not [x for x in f if x["severity"] == "high"]
    assert all(x["severity"] == "low" for x in f)


def test_b1_no_flag_short_or_all_integer_column():
    short = [3.0, 3.2, 2.5, 2.8, 2.9, 2.2]           # < 12
    ints = [int(v) for v in range(100, 118)]          # all-integer, n=18 < 25
    panels = {
        ("A.xls", "Figure 1a"): {"s": short, "i": list(ints)},
        ("B.xls", "Figure 2a"): {"s": list(short), "i": list(ints)},
    }
    f = _run_b1(panels)
    assert not f
    assert _b1_oracle(panels) == set()


def test_b1_serial_dilution_axis_not_flagged():
    # regression: _column_axis_like rejected only arithmetic ladders, so a geometric
    # serial-dilution axis shared across two dose-response panels was flagged HIGH (FP).
    serial = [100.0 / (2 ** i) for i in range(14)]
    other_a = [1.1 * i + 0.3 for i in range(14)]
    other_b = [9.0 - 0.2 * i for i in range(14)]
    panels = {
        ("A.xls", "Figure 1a"): {"dose": list(serial), "m": other_a},
        ("B.xls", "Figure 2a"): {"dose": list(serial), "m": other_b},
    }
    f = _run_b1(panels)
    assert not [x for x in f if x["severity"] == "high"], "a shared serial-dilution axis is benign"


def test_b1_rule_wording_is_honest_about_precision():
    dup = [3.0, 3.2, 2.5, 2.8, 2.9, 2.2, 5.0, 5.2, 4.5, 4.8, 4.9, 4.2, 6.1, 6.3, 5.7]
    panels = {
        ("F3.xls", "Figure 3b"): {"x": dup},
        ("F9.xls", "Extended Data Fig. 9d"): {"y": list(dup)},
    }
    hi = [x for x in _run_b1(panels) if x["severity"] == "high"]
    assert hi and "byte-identical" not in hi[0]["rule"]
    assert "6 decimal places" in hi[0]["rule"]


def test_b1_single_sheet_early_exit():
    from paperconan._audit import detect_cross_sheet_column_duplicates
    one = {("A.xls", "Figure 1a"): _sheet_from_cols(["a", "b"], [[1.1 * i + 0.3 for i in range(14)],
                                                                 [9.0 - 0.2 * i for i in range(14)]])}
    assert detect_cross_sheet_column_duplicates(one) == []
