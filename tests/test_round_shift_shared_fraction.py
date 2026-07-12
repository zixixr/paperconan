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
