"""Within-row repeated segment — the within-row member of the recurring-vector family.

`detect_recurring_row_vectors` flags a high-information numeric tuple that recurs across >=2
figures. Its within-row sibling — the SAME contiguous high-precision segment appearing twice
in ONE row at non-overlapping columns — had no detector: JCI196944 Fig S2H's CNO row carries
the identical 5-value tuple under both the Saline and the METH group. Two independent cohorts
cannot yield the same high-precision tuple; the repeat is a copy fingerprint. Signal, not
verdict — same family, same gates (>=3 distinct values, no ladders), extended to one row.
"""
from __future__ import annotations

from paperconan import scan_dir
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


def test_detects_within_row_repeated_segment():
    # seg appears at cols 2-6 and 8-12 (non-overlapping), a spacer value between the groups.
    row = [1.785714, *SEG, 5.714286, *SEG]
    findings = detect_recurring_row_vectors({("JCI196944.xlsx", "Supplemental Figure 2"): _row_sheet(row)})
    wr = [f for f in findings if f["kind"] == "within_row_repeated_segment"]
    assert len(wr) == 1, f"expected one within-row repeated segment, got {findings}"
    assert wr[0]["severity"] == "high"


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
