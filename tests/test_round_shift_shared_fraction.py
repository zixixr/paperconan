"""Round-shift + shared-fraction detector (class C/D: two groups differ by multiples of 10).

`integer_diff_shared_fraction` (B5) only fires at >=4 significant fraction digits. A very
common source-data fingerprint is two groups whose cells share the SAME 2-decimal fraction
while the integer parts differ by non-zero MULTIPLES OF 10 (a copy-then-nudge-by-round-
numbers). At 2 decimals a bare shared fraction is less distinctive, but the "all differences
are multiples of 10" constraint has no benign additive transform, so the pair is still a
data-inconsistency signal worth an author's explanation — not a verdict.
"""
from __future__ import annotations

from paperconan._audit import detect_relations
from paperconan._sheet import Sheet


def test_detects_round_multiple_of_ten_shift_at_two_decimals():
    # two DIFFERENT groups: b = a + [60,-10,-20,20,70,-20] (all multiples of 10), same .xx fraction
    a = [72.34, 127.58, 148.86, 117.91, 83.26, 95.22]
    b = [132.34, 117.58, 128.86, 137.91, 153.26, 75.22]
    rows = [["idx", "NEUWT", "EOSPAD4"]]
    for i, (x, y) in enumerate(zip(a, b), 1):
        rows.append([i, x, y])
    sheet = Sheet.from_rows(rows)

    findings = detect_relations(sheet, 1, 7, 1, 3, ["NEUWT", "EOSPAD4"])
    rs = [f for f in findings if f["kind"] == "round_shift_shared_fraction"]
    assert len(rs) == 1, f"expected round-shift finding, got {findings}"
    assert rs[0]["severity"] == "high"


def test_no_false_positive_on_independent_two_decimal_columns():
    a = [72.34, 127.58, 148.86, 117.91, 83.26, 95.22]
    b = [41.19, 88.63, 12.07, 155.42, 63.91, 100.28]   # unrelated fractions, non-round diffs
    rows = [["idx", "A", "B"]]
    for i, (x, y) in enumerate(zip(a, b), 1):
        rows.append([i, x, y])
    sheet = Sheet.from_rows(rows)

    findings = detect_relations(sheet, 1, 7, 1, 3, ["A", "B"])
    assert [f for f in findings if f["kind"] == "round_shift_shared_fraction"] == []


def _pair_findings(a, b):
    rows = [["idx", "A", "B"]]
    for i, (x, y) in enumerate(zip(a, b), 1):
        rows.append([i, x, y])
    sheet = Sheet.from_rows(rows)
    return detect_relations(sheet, 1, 1 + len(a), 1, 3, ["A", "B"])


def _has_round_shift(a, b):
    return any(f["kind"] == "round_shift_shared_fraction" for f in _pair_findings(a, b))


def test_mostly_integer_column_with_few_decimals_does_not_fire():
    # 15 integer rows shifted by tens + only 3 fractional rows: the fractional evidence is
    # too thin (< 0.7n) even though 'differences are multiples of 10' holds for all rows.
    a = [70, 120, 150, 110, 80, 90, 130, 160, 40, 200, 60, 100, 30, 170, 140,
         72.34, 127.58, 148.86]
    b = [130, 110, 130, 130, 150, 70, 160, 120, 60, 180, 110, 60, 90, 130, 100,
         132.34, 117.58, 128.86]
    assert not _has_round_shift(a, b)


def test_single_non_multiple_of_ten_diff_blocks_firing():
    # one row shifted by 7 (not a multiple of 10) must break the pattern entirely.
    a = [72.34, 127.58, 148.86, 117.91, 83.26, 95.22]
    b = [132.34, 117.58, 128.86, 137.91, 153.26, 102.22]   # last diff = +7, not mult of 10
    assert not _has_round_shift(a, b)


def test_multiples_of_hundred_fire():
    # 100/200 are multiples of 10 too — a coarser round shift still fires.
    a = [72.34, 127.58, 148.86, 117.91, 83.26, 95.22]
    b = [172.34, 227.58, 48.86, 317.91, 83.26 + 100, 95.22 - 200]
    assert _has_round_shift(a, b)


def test_distinct_fraction_floor_of_three():
    # only 2 distinct fractions across the shared rows → not distinctive enough → no fire.
    a = [10.25, 20.50, 30.25, 40.50, 50.25, 60.50]
    b = [20.25, 30.50, 40.25, 50.50, 60.25, 70.50]   # all +10, but only .25/.50 fractions
    assert not _has_round_shift(a, b)


def test_constant_multiple_of_ten_offset_is_claimed_by_constant_offset():
    # a CONSTANT +10 offset is the constant_offset case, not round_shift (ordering guard).
    a = [72.34, 127.58, 148.86, 117.91, 83.26, 95.22]
    b = [x + 10 for x in a]
    kinds = {f["kind"] for f in _pair_findings(a, b)}
    assert "constant_offset" in kinds
    assert "round_shift_shared_fraction" not in kinds


def test_integer_only_multiple_of_ten_shift_does_not_fire():
    # integer counts shifted by 10s (no genuine decimal fraction) must NOT fire — that is
    # ordinary integer data, not a preserved-fraction copy fingerprint.
    a = [70, 120, 150, 110, 80, 90]
    b = [130, 110, 130, 130, 150, 70]
    rows = [["idx", "A", "B"]]
    for i, (x, y) in enumerate(zip(a, b), 1):
        rows.append([i, x, y])
    sheet = Sheet.from_rows(rows)

    findings = detect_relations(sheet, 1, 7, 1, 3, ["A", "B"])
    assert [f for f in findings if f["kind"] == "round_shift_shared_fraction"] == []
