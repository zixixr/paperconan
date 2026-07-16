"""Row-PAIR shared fractional tail — copy-then-integer-shift between two rows.

`integer_diff_shared_fraction` finds two COLUMNS whose rows share a decimal fraction while
differing by integers. Its row-oriented twin — two ROWS that share the decimal fraction at
aligned columns while the integer parts differ — had no detector (the JCI201090 DU145/C4-2
case: a concentration row reused at another concentration with the integers rewritten). A
run of aligned columns each sharing a >=4-digit tail is ~(1e-4)^run by chance, so a >=3
column run of distinct shared tails is a near-zero-chance copy-then-shift. Signal, not
verdict — neutral language.
"""
from __future__ import annotations

from paperconan import scan_dir
from paperconan._audit import detect_row_pair_shared_fraction
from paperconan._sheet import Sheet


# JCI201090 Fig3D C4-2 Isotype ADC: 20 nM and 100 nM rows share .27037/.85351/.86076 over
# the first 3 columns (integer diffs 10/2/4), the rest differ.
R20 = [95.27037, 90.85351, 91.86076, 97.72367, 98.27, 92.26044, 70.60717, 90.96432, 79.9286]
R100 = [85.27037, 88.85351, 87.86076, 96.0847, 98.81632, 102.026, 21.42858, 21.10715, 21.85715]


def _hp(n, seed):
    # row-specific fractional parts: two different seeds give two independent tail sets
    return [10.0 + ((k * 13 + seed) % 83)
            + ((k * 7919 + seed * 104729) % 1000000) / 1000003.7 for k in range(n)]


def test_detects_row_pair_shared_fraction():
    sheet = Sheet.from_rows([
        ["Concentration", "Isotype", "", "", "SI-B001", "", "", "BL-B01D1", "", ""],
        ["0.1", 98.92209, 99.61126, 98.49799, 100.6, 99.1, 98.6, 92.0, 92.8, 96.4],
        ["20", *R20],
        ["100", *R100],
        ["400", 51.74713, 55.32303, 54.64835, 98.8, 86.7, 87.4, 14.3, 13.7, 14.3],
    ])
    findings = detect_row_pair_shared_fraction({("JCI201090.xlsx", "Fig3D"): sheet})
    assert len(findings) == 1, f"expected one row-pair shared-fraction finding, got {findings}"
    f = findings[0]
    assert f["kind"] == "shared_fraction_row_pair"
    assert f["severity"] == "high"
    assert f["run_length"] >= 3


def test_no_false_positive_on_independent_rows():
    sheet = Sheet.from_rows([
        ["a", *_hp(9, 1)],
        ["b", *_hp(9, 2)],
    ])
    assert detect_row_pair_shared_fraction({("f.xlsx", "S1"): sheet}) == []


def test_no_false_positive_on_shared_small_denominator():
    # Two rows of thirds (n/3) share .333/.667 across integers — a division artifact.
    a = [6.333333333, 9.666666667, 12.333333333, 5.666666667]
    b = [16.333333333, 19.666666667, 22.333333333, 15.666666667]
    sheet = Sheet.from_rows([["a", *a], ["b", *b]])
    assert detect_row_pair_shared_fraction({("f.xlsx", "S2"): sheet}) == []


def test_no_false_positive_on_identical_rows():
    # Same integer AND same fraction on every column = a duplicate row (identical_row_reuse's
    # job), not copy-then-SHIFT — the integer part must differ.
    v = _hp(9, 1)
    sheet = Sheet.from_rows([["a", *v], ["b", *list(v)]])
    assert detect_row_pair_shared_fraction({("f.xlsx", "S3"): sheet}) == []


def test_genuine_short_run_not_masked_by_longer_benign_run():
    # The longest contiguous run is a small-denominator (1/3) block that fails the gate; a
    # DISTINCT arbitrary 3-column copy-shift elsewhere must still fire (review #1).
    a = [10.333333, 11.333333, 12.333333, 13.333333, 14.333333, 99.0,
         20.316768, 30.849559, 40.647899]
    b = [20.333333, 21.333333, 22.333333, 23.333333, 24.333333, 88.0,
         52.316768, 61.849559, 47.647899]   # varied integer diffs 32/31/7 -> a real copy-shift
    sheet = Sheet.from_rows([["a", *a], ["b", *b]])
    f = detect_row_pair_shared_fraction({("f.xlsx", "S4"): sheet})
    assert len(f) == 1 and f[0]["run_length"] >= 3, f"genuine short run was masked: {f}"


def test_constant_integer_offset_row_pair_not_flagged():
    # rowB = rowA + 5 (a single integer difference) is a constant offset (constant_offset's
    # job), not a copy-then-SHIFT — needs >=2 distinct integer diffs like the column twin (#2).
    a = [10.316768, 11.849559, 12.647899, 13.173653, 14.508241, 15.930517]
    b = [x + 5 for x in a]
    sheet = Sheet.from_rows([["a", *a], ["b", *b]])
    assert detect_row_pair_shared_fraction({("f.xlsx", "S5"): sheet}) == []


def test_scan_dir_surfaces_row_pair_shared_fraction(tmp_path):
    rows = [
        ["Concentration", "Isotype", "", "", "SI", "", "", "BL", "", ""],
        ["20", *R20],
        ["100", *R100],
    ]
    data = tmp_path / "data"
    data.mkdir()
    (data / "s.csv").write_text(
        "\n".join(",".join(str(x) for x in r) for r in rows) + "\n", encoding="utf-8")
    res = scan_dir(str(data), str(tmp_path / "out"), write_html=True)
    hits = [f for f in res.get("cross_sheet_findings", []) or []
            if f.get("kind") == "shared_fraction_row_pair"]
    assert hits, "scan_dir did not surface the row-pair shared fraction"
    html = (tmp_path / "out" / "report.html").read_text(encoding="utf-8")
    assert "27037" in html
