"""Within-row repeated segment — the within-row member of the recurring-vector family.

`detect_recurring_row_vectors` flags a high-information numeric tuple that recurs across >=2
figures. Its within-row sibling — the SAME contiguous high-precision segment appearing twice
in ONE row at non-overlapping columns — had no detector: JCI196944 Fig S2H's CNO row carries
the identical 5-value tuple under both the Saline and the METH group. Two independent cohorts
cannot yield the same high-precision tuple; the repeat is a copy fingerprint. Signal, not
verdict — same family, same gates (>=3 distinct values, no ladders), extended to one row.
"""
from __future__ import annotations

import json

from paperconan import scan_dir, write_html_report
from paperconan._audit import detect_recurring_row_vectors
from paperconan._sheet import Sheet

SEG = [3.238866, 1.724138, 3.418803, 0.727273, 2.380952]   # JCI196944 Fig S2H CNO segment


def _fill(n, seed):
    return [11.0 + ((k * 13 + seed) % 71) + ((k * 7919 + seed * 104729) % 1000000) / 1000003.7
            for k in range(n)]


def _row_sheet(row, name="Supplemental Figure 2"):
    # >=2 data rows so find_numeric_blocks forms a block; the repeat lives in ONE of them.
    n = len(row)
    return Sheet.from_rows([
        [name] + [f"c{i}" for i in range(n)],
        ["Veh", *_fill(n, 3)],
        ["CNO", *row],
        ["X", *_fill(n, 7)],
    ])


def _coordinate_sheet():
    rows = [
        [f"row-{row + 1}", *_fill(10, row + 11)]
        for row in range(18)
    ]
    segment = [0.5024, 0.4866, 0.2077, 0.4269]
    rows.append(["target", *segment, 9.1234, 8.2345, *segment])
    return Sheet.from_rows(rows)


def _sparse_coordinate_sheet():
    rows = [
        [f"row-{row + 1}", *_fill(17, row + 31)]
        for row in range(18)
    ]
    segment = [0.5024, 0.4866, 0.2077, 0.4269]
    rows.append([
        "target",
        segment[0], "gap", segment[1], "gap",
        segment[2], "gap", segment[3],
        9.1234, 8.2345,
        segment[0], "gap", segment[1], "gap",
        segment[2], "gap", segment[3],
    ])
    return Sheet.from_rows(rows)


def test_detects_within_row_repeated_segment():
    # seg appears at cols 2-6 and 8-12 (non-overlapping), a spacer value between the groups.
    row = [1.785714, *SEG, 5.714286, *SEG]
    findings = detect_recurring_row_vectors({("JCI196944.xlsx", "Supplemental Figure 2"): _row_sheet(row)})
    wr = [f for f in findings if f["kind"] == "within_row_repeated_segment"]
    assert len(wr) == 1, f"expected one within-row repeated segment, got {findings}"
    assert wr[0]["severity"] == "high"


def test_repeated_segment_reports_exact_excel_coordinates():
    findings = detect_recurring_row_vectors({
        ("synthetic.xlsx", "Fig. 2"): _coordinate_sheet(),
    })
    finding = next(
        item
        for item in findings
        if item["kind"] == "within_row_repeated_segment"
    )

    assert finding["row"] == 19
    assert finding["row_idx"] == 18
    assert finding["occurrences"] == [
        {
            "row": 19,
            "col_start": 2,
            "col_end": 5,
            "columns": [2, 3, 4, 5],
            "ranges": ["B:E"],
            "range": "B:E",
        },
        {
            "row": 19,
            "col_start": 8,
            "col_end": 11,
            "columns": [8, 9, 10, 11],
            "ranges": ["H:K"],
            "range": "H:K",
        },
    ]
    assert "within row 19" in finding["rule"]
    assert "(B:E ↔ H:K)" in finding["rule"]


def test_html_summary_shows_repeated_segment_coordinates(tmp_path):
    finding = next(
        item
        for item in detect_recurring_row_vectors({
            ("synthetic.xlsx", "Fig. 2"): _coordinate_sheet(),
        })
        if item["kind"] == "within_row_repeated_segment"
    )
    report = tmp_path / "report.html"
    write_html_report({
        "input_dir": "synthetic",
        "n_files": 1,
        "relations_blocks": [],
        "cross_sheet_findings": [finding],
        "digit_distribution": [],
        "decimal_endings": [],
    }, str(report))

    html = report.read_text(encoding="utf-8")
    summary = html.split("<summary>", 1)[1].split("</summary>", 1)[0]

    assert "row 19" in summary
    assert "B:E ↔ H:K" in summary


def test_sparse_repeated_segment_reports_exact_physical_columns():
    finding = next(
        item
        for item in detect_recurring_row_vectors({
            ("synthetic.xlsx", "Fig. 2"): _sparse_coordinate_sheet(),
        })
        if item["kind"] == "within_row_repeated_segment"
    )

    assert finding["occurrences"] == [
        {
            "row": 19,
            "col_start": 2,
            "col_end": 8,
            "columns": [2, 4, 6, 8],
            "ranges": ["B", "D", "F", "H"],
            "range": "B,D,F,H",
        },
        {
            "row": 19,
            "col_start": 11,
            "col_end": 17,
            "columns": [11, 13, 15, 17],
            "ranges": ["K", "M", "O", "Q"],
            "range": "K,M,O,Q",
        },
    ]
    assert "(B,D,F,H ↔ K,M,O,Q)" in finding["rule"]


def test_no_false_positive_on_non_repeating_row():
    row = [1.785714, *SEG, 5.714286, 9.111111, 8.222222, 7.333333, 6.444444, 5.555556]
    assert not [f for f in detect_recurring_row_vectors(
        {("f.xlsx", "Fig 1"): _row_sheet(row)}) if f["kind"] == "within_row_repeated_segment"]


def test_no_false_positive_on_overlapping_window():
    # A run like [x, x, x, x, x] repeats overlapping windows but is a single constant block,
    # not two non-overlapping copies; and it is low-information (patterned). Must not fire.
    row = [2.5] * 10
    assert not [f for f in detect_recurring_row_vectors(
        {("f.xlsx", "Fig 1"): _row_sheet(row)}) if f["kind"] == "within_row_repeated_segment"]


def test_no_false_positive_on_short_or_low_info_repeat():
    # A 3-value low-info repeat (below min_k / patterned) must not fire.
    row = [1.0, 2.0, 1.0, 2.0, 1.0, 2.0]
    assert not [f for f in detect_recurring_row_vectors(
        {("f.xlsx", "Fig 1"): _row_sheet(row)}) if f["kind"] == "within_row_repeated_segment"]


def test_no_false_positive_on_quantized_grid_row():
    # A tiny value pool (like JCI182394 Fig11J's k/19 body weights): the tuple (a,b,c,d)
    # repeats twice, but each value recurs 5x across the row (pool is small) — far more than the
    # two copies. The per-row frequency gate (freq >> copies) must suppress it.
    a, b, c, d = 110.5263, 94.73684, 105.2632, 89.47368
    sp = 999.1111                                       # distinct spacer, breaks up the tuple
    row = [a, b, c, d] + [a, sp, b, sp, c, sp, d, sp] * 3 + [a, b, c, d]  # a,b,c,d each 5x
    assert not [f for f in detect_recurring_row_vectors(
        {("JCI182394.xlsx", "Fig.11"): _row_sheet(row)})
        if f["kind"] == "within_row_repeated_segment"]


def test_no_false_positive_on_small_magnitude_quantized_pool():
    # Same quantized-pool structure but at ~1e-4 magnitude (molar concentrations / proportions):
    # the frequency bucket must use the SAME quantization as the window key, or the lookup
    # misses and the gate leaks (review I2).
    a, b, c, d = 0.0001105263, 0.00009473684, 0.0001052632, 0.00008947368
    sp = 0.0009991111
    row = [a, b, c, d] + [a, sp, b, sp, c, sp, d, sp] * 3 + [a, b, c, d]  # a,b,c,d each 5x
    assert not [f for f in detect_recurring_row_vectors(
        {("f.xlsx", "Fig 1"): _row_sheet(row)})
        if f["kind"] == "within_row_repeated_segment"]


def test_genuine_repeat_kept_despite_a_few_incidental_extras():
    # A real copied 5-tuple must NOT be dropped just because one of its values appears a couple
    # extra times elsewhere in a wide row — suppress only when freq >> copies (review I3).
    seg = [1.724138, 3.418803, 3.238866, 0.727273, 2.380952]   # popular value CENTRAL (no escape)
    row = [*seg, 9.111111, 3.238866, 8.222222, 3.238866, 7.333333, *seg]  # 3.238866 freq = 4
    wr = [f for f in detect_recurring_row_vectors({("f.xlsx", "Fig 1"): _row_sheet(row)})
          if f["kind"] == "within_row_repeated_segment"]
    assert len(wr) == 1, f"genuine repeat wrongly suppressed: {wr}"


def test_scan_dir_surfaces_within_row_repeated_segment(tmp_path):
    row = [1.785714, *SEG, 5.714286, *SEG]
    data = tmp_path / "data"
    data.mkdir()
    lines = ["Veh," + ",".join(str(v) for v in _fill(len(row), 3)),
             "CNO," + ",".join(str(v) for v in row),
             "X," + ",".join(str(v) for v in _fill(len(row), 7))]
    (data / "s.csv").write_text("\n".join(lines) + "\n", encoding="utf-8")
    res = scan_dir(str(data), str(tmp_path / "out"), write_html=True)
    assert [f for f in res.get("cross_sheet_findings", []) or []
            if f.get("kind") == "within_row_repeated_segment"]


# --- the floor is graded by what the values are, not only by how many there are ---

# Six decimals, no ladder, three distinct values: as a repeat, this is a lot of agreement.
_FINE = [4.176382, 9.028415, 1.593047]
# The same three numbers as a reader would meet them at two decimals. Same COUNT, far less
# said -- which is the asymmetry a count-only floor cannot see.
_COARSE = [round(v, 2) for v in _FINE]


def _three_wide_row(segment):
    """One row carrying `segment` twice at non-overlapping columns, spacers between."""
    return [2.719281, *segment, 6.451927, 8.130644, *segment, 3.907516]


def test_a_three_wide_high_precision_repeat_is_found():
    """Three values recorded finely and repeated exactly is more agreement than four coarse
    ones, and the length-only floor ranked it the other way: it rejected this and accepted
    the coarser, longer run."""
    findings = detect_recurring_row_vectors({
        ("synthetic.xlsx", "Figure 1"): _row_sheet(_three_wide_row(_FINE)),
    })

    wr = [f for f in findings if f["kind"] == "within_row_repeated_segment"]
    assert len(wr) == 1, f"expected the three-wide fine repeat, got {findings}"
    assert wr[0]["vector"] == _FINE


def test_a_three_wide_coarse_repeat_is_not_found():
    """The control that makes the test above about PRECISION and not about length.

    Same shape, same count, same positions -- only the number of recorded decimals differs.
    If this fired too, the change would be 'shorten the floor', which is the thing measured
    to cost more than it returns."""
    findings = detect_recurring_row_vectors({
        ("synthetic.xlsx", "Figure 1"): _row_sheet(_three_wide_row(_COARSE)),
    })

    assert [f for f in findings if f["kind"] == "within_row_repeated_segment"] == []


def test_the_short_pass_cannot_spend_the_budget_the_wider_widths_use():
    """Adding a width must not take reach away from the widths that were already there.

    The within-row scan is bounded by a window budget, and the budget is charged per window
    per start position. Charging the new short width to the SAME budget cost one window more
    at every start, so a large sheet ran out sooner and the pass reported LESS than before
    the capability existed -- measured, as a four-wide finding that disappeared from a corpus
    file. The short width has its own counter now.

    Checked at a budget just above what the wider widths need to reach the second copy: with
    the counters separate the repeat is found, and it is not found when the short width is
    allowed to spend from the same pot. A first version of this test set the SHORT budget to
    zero instead, which the very code path it was guarding does not read -- it passed either
    way, and the data it used had four decimals, too coarse for a short window to be indexed
    at all. It was measuring nothing, twice over.
    """
    import os
    import subprocess
    import sys

    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    # Every cell finely recorded, so short windows really are indexed; the repeated segment
    # sits late in the row, where the budget decides whether the scan still reaches it.
    filler = [round(1.0 + i * 0.7 + i * i * 1e-5, 6) for i in range(60)]
    segment = [7.104391, 3.882057, 9.240118, 2.665903]
    row = list(filler)
    row[30:34] = segment
    row[40:44] = segment
    code = (
        "import json, sys;"
        "sys.path.insert(0, 'src');"
        "from paperconan._audit import detect_recurring_row_vectors;"
        "from paperconan._sheet import Sheet;"
        f"row = {row!r};"
        "rows = [['h'] + ['c%d' % i for i in range(len(row))], ['b'] + row];"
        "out = detect_recurring_row_vectors({('s.xlsx', 'Fig. 1'): Sheet.from_rows(rows)});"
        "print(json.dumps(sorted(len(f['vector']) for f in out"
        " if f['kind'] == 'within_row_repeated_segment')))"
    )
    out = subprocess.check_output(
        [sys.executable, "-c", code],
        env={**os.environ, "PAPERCONAN_WITHIN_ROW_VEC_BUDGET": "210"},
        text=True,
        cwd=root,
    )

    assert json.loads(out.strip()) == [4], out


def test_a_short_candidate_does_not_replace_the_wider_repeat_it_sits_inside():
    """A shorter statement must be added beside a longer one, never put in its place.

    The candidate pool is ranked by copy count first. A three-wide window inside a genuine
    four-wide repeat can also match at a third place where the fourth column disagrees, so
    it has MORE copies than the four-wide vector, sorted ahead of it, and the four-wide
    finding was then dropped against it as an overlapping duplicate. What reached the
    reader was three values at three places -- with the fourth column's agreement, the
    stronger half of the evidence, nowhere in the output.
    """
    wide = [4.176382, 9.028415, 1.593047, 6.702914]
    row = (_fill(10, 3) + wide + _fill(6, 5) + wide + _fill(6, 7)
           + wide[:3] + _fill(6, 9))

    findings = detect_recurring_row_vectors({
        ("synthetic.xlsx", "Figure 1"): _row_sheet(row),
    })

    wr = [f for f in findings if f["kind"] == "within_row_repeated_segment"]
    assert wr, f"expected the four-wide repeat, got {findings}"
    assert wr[0]["vector"] == wide, wr[0]["vector"]


def test_a_coarse_row_does_not_spend_the_short_budget_before_a_fine_repeat_in_it():
    """The precision check at window generation, not the one after it.

    Both gates reject a coarse short segment, so removing either alone leaves the coarse
    control test green -- but only the generation-time one keeps a doomed window from being
    INDEXED, and therefore from spending the short budget. Without it a row of coarse
    filler burns that budget before the scan reaches a genuine fine repeat late in the same
    row, and the finding disappears: the capability subtracting coverage again, one layer
    below where that was first found.
    """
    import os
    import subprocess
    import sys

    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    coarse = [round(3.0 + i * 0.37, 2) for i in range(60)]
    segment = [7.104391, 3.882057, 9.240118]
    row = coarse + segment + [8.15, 2.94] + segment
    code = (
        "import json, sys;"
        "sys.path.insert(0, 'src');"
        "from paperconan._audit import detect_recurring_row_vectors;"
        "from paperconan._sheet import Sheet;"
        f"row = {row!r};"
        "rows = [['h'] + ['c%d' % i for i in range(len(row))], ['b'] + row];"
        "out = detect_recurring_row_vectors({('s.xlsx', 'Fig. 1'): Sheet.from_rows(rows)});"
        "print(json.dumps(sorted(len(f['vector']) for f in out"
        " if f['kind'] == 'within_row_repeated_segment')))"
    )
    out = subprocess.check_output(
        [sys.executable, "-c", code],
        env={**os.environ, "PAPERCONAN_WR_SHORT_SEGMENT_BUDGET": "50"},
        text=True,
        cwd=root,
    )

    assert 3 in json.loads(out.strip()), out


def test_a_row_too_narrow_for_two_wide_copies_still_reaches_the_short_pass():
    """The row-level precondition has to use the shortest width the pass will consider.

    It asked for room for two copies at the ORDINARY width, so a row with just enough cells
    for two three-wide copies was dropped before any short window was built -- the floor
    change reaching the window loop but not the row that would have used it.
    """
    segment = [2.481937, 5.914026, 8.336571]
    row = segment + segment   # six numeric cells: too narrow for two four-wide copies

    findings = detect_recurring_row_vectors({
        ("synthetic.xlsx", "Figure 1"): _row_sheet(row),
    })

    wr = [f for f in findings if f["kind"] == "within_row_repeated_segment"]
    assert len(wr) == 1, f"expected the narrow-row repeat, got {findings}"
    assert wr[0]["vector"] == segment


def test_folding_a_repeat_that_occurs_elsewhere_is_recorded():
    """Selection is unchanged; the silence is what this fixes.

    The fold collapses the overlapping windows one physical repeat produces, which is
    right. It is not right when the folded candidate also repeats where the surviving
    finding never reaches: that place corroborates nothing on the page and leaves with the
    candidate. Rescuing the candidate whole was tried and withdrawn -- it then suppressed
    later candidates and dropped findings the fold used to keep, without moving the finding
    count, so the cure had the disease. What is recorded instead is that the pass held
    something back, which is the difference between a page that is incomplete and a page
    that looks complete.
    """
    from paperconan._coverage import ScanCoverage

    s0, s1, s2 = 4.176382, 9.028415, 1.593047
    wide = [6.702914, s0, s1, s2]
    row = (_fill(10, 3) + wide + _fill(20, 5) + [s0, s1, s2]
           + _fill(18, 7) + wide + _fill(6, 9))
    coverage = ScanCoverage(files_discovered=1)

    findings = [f for f in detect_recurring_row_vectors(
        {("synthetic.xlsx", "Figure 1"): _row_sheet(row)}, coverage=coverage,
    ) if f["kind"] == "within_row_repeated_segment"]

    # Unchanged: one finding, the wider repeat, exactly as before this note existed.
    assert [len(f["vector"]) for f in findings] == [4]
    reasons = [item.get("reason") for item in coverage.to_dict()["limitations"]]
    assert "detector_within_row_folded_independent_repeat" in reasons, reasons


def test_a_plain_fold_records_nothing():
    """The control. A long repeat yields many shifted windows over the SAME places, and
    folding those is the pass working -- if that were recorded too, the note would appear
    on ordinary input and mean nothing."""
    from paperconan._coverage import ScanCoverage

    segment = [7.104391, 3.882057, 9.240118, 2.665903, 5.417620]
    row = _fill(8, 3) + segment + _fill(6, 5) + segment + _fill(8, 7)
    coverage = ScanCoverage(files_discovered=1)

    detect_recurring_row_vectors(
        {("synthetic.xlsx", "Figure 1"): _row_sheet(row)}, coverage=coverage)

    reasons = [item.get("reason") for item in coverage.to_dict()["limitations"]]
    assert "detector_within_row_folded_independent_repeat" not in reasons, reasons
