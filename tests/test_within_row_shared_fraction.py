"""Within-row shared fractional tail — the copy-then-shift fingerprint inside ONE row.

Two cells of the same row that share a long high-precision fractional tail while their
integer parts differ (e.g. 20.316768 and 102.316768) are a copy-then-shift: a value or a
whole segment reused with the integer part rewritten but the decimals left intact. The
existing shared-fraction detectors (`integer_diff_shared_fraction`,
`round_shift_shared_fraction`) only compare two COLUMNS; `within_table_fraction_reuse`
compares two BLOCKS — none looks across the columns of a single row. Signal, not verdict.
"""
from __future__ import annotations

from paperconan import scan_dir
from paperconan._audit import detect_within_row_shared_fraction
from paperconan._sheet import Sheet


# JCI195506 (CHI3L1) Fig 2F, TNF-α row: a 3-cell segment reused with integers rewritten.
TNF = [22.49183467, 25.04654933, 23.831478, 25.925474,
       20.316768, 162.14990133, 132.81763667,
       102.316768, 163.14990133, 138.81763667,
       54.57891, 43.86014933, 68.66210333, 44.22101267, 52.025184]


def test_detects_within_row_shared_fraction_segment():
    sheet = Sheet.from_rows([
        ["Figure 2F", "Ctrl-IgG+Veh", "AQP4-IgG+Veh", "AQP4-IgG+CHI"],
        ["TNF-α (pg/mg)", *TNF],
    ])
    findings = detect_within_row_shared_fraction({("JCI195506.xlsx", "Figure 2"): sheet})
    assert len(findings) == 1, f"expected one within-row shared-fraction finding, got {findings}"
    f = findings[0]
    assert f["kind"] == "within_row_shared_fraction"
    assert f["severity"] == "high"
    assert f["n_groups"] >= 3          # .316768, .14990133, .81763667
    assert f["row"] == "TNF-α (pg/mg)"


def test_no_false_positive_on_independent_high_precision_row():
    # A row of independent 6-decimal measurements shares no long fractional tail.
    row = [10 + i + ((i * 616157 + 7919) % 1_000_000) / 1_000_000 for i in range(15)]
    sheet = Sheet.from_rows([["h"] + [f"m{i}" for i in range(15)], ["r", *row]])
    assert detect_within_row_shared_fraction({("f.xlsx", "S1"): sheet}) == []


def test_no_false_positive_on_short_shared_tail():
    # Two values sharing only a 2-digit ending (.37) collide by chance — must not fire.
    sheet = Sheet.from_rows([["r", 12.37, 55.37, 3.14159, 2.71828, 1.61803]])
    assert detect_within_row_shared_fraction({("f.xlsx", "S2"): sheet}) == []


def test_shared_tail_with_same_integer_is_not_flagged():
    # Same integer AND same fraction = a duplicate VALUE (caught elsewhere), not copy-shift.
    sheet = Sheet.from_rows([["r", 20.316768, 20.316768, 5.111111, 6.222222, 7.333333]])
    assert detect_within_row_shared_fraction({("f.xlsx", "S3"): sheet}) == []


def test_no_false_positive_on_small_denominator_fractions():
    # Triplicate means (n/3 -> .333/.667) and other small-denominator fractions (k/13 ->
    # .923076) trivially share a tail across different integers — a division artifact, not a
    # copy. Real JCI panels (JCI200564 Fig.1, JCI200225 Fig.S7F) false-positived here.
    thirds = Sheet.from_rows([["r", 6.333333333, 9.333333333, 2.666666667, 12.666666667,
                               3.141592653, 1.414213562]])
    assert detect_within_row_shared_fraction({("f.xlsx", "S5"): thirds}) == []
    # k/13: 12/13 = 0.923076923, shared across two integers, is still an artifact.
    k13 = Sheet.from_rows([["r", 5.923076923, 12.923076923, 3.076923077, 8.076923077,
                            2.718281828, 1.732050808]])
    assert detect_within_row_shared_fraction({("f.xlsx", "S6"): k13}) == []


def test_no_false_positive_on_float_noise_tail_in_millions():
    # Values in the millions with a genuine SHORT (3-dp) tail: formatting the whole value at
    # 10 decimals exceeds float64 precision, so the low decimals are representation NOISE.
    # The real tail is 3 digits (<6), so this benign coincidence must NOT fire (review bug 1).
    sheet = Sheet.from_rows([["reads", 1500000.137, 2500000.137, 3500000.137,
                              4500000.137, 1234567.891, 7654321.234]])
    assert detect_within_row_shared_fraction({("counts.xlsx", "S7"): sheet}) == []


def test_no_false_positive_on_dyadic_fraction():
    # 1/128 = 0.0078125 (denominator 128 > 64) is a fixed-point/quantization artifact shared
    # across integers, not a copied tail — must not fire (review bug 2).
    sheet = Sheet.from_rows([["r", 3.0078125, 17.0078125, 42.0078125, 100.0078125,
                              2.718281828, 1.414213562]])
    assert detect_within_row_shared_fraction({("f.xlsx", "S8"): sheet}) == []


def test_large_magnitude_values_skipped():
    # >=1e7: the shared digits are read-precision noise, not a copied tail.
    sheet = Sheet.from_rows([["r", 1e8 + 0.316768, 2e8 + 0.316768, 3.14159, 2.71828, 1.61803]])
    assert detect_within_row_shared_fraction({("f.xlsx", "S4"): sheet}) == []


def test_scan_dir_surfaces_within_row_shared_fraction(tmp_path):
    data = tmp_path / "data"
    data.mkdir()
    (data / "s.csv").write_text(
        "TNF-α (pg/mg)," + ",".join(str(v) for v in TNF) + "\n", encoding="utf-8")
    res = scan_dir(str(data), str(tmp_path / "out"), write_html=True)
    hits = [f for f in res.get("cross_sheet_findings", []) or []
            if f.get("kind") == "within_row_shared_fraction"]
    assert hits, "scan_dir did not surface the within-row shared fraction"
    # the shared tail and full-precision values must be visible in the human-facing report
    html = (tmp_path / "out" / "report.html").read_text(encoding="utf-8")
    assert "316768" in html, "within-row shared-fraction evidence missing from HTML report"
