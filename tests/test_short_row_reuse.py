"""Short high-precision row reuse across blocks / isolated single-row panels.

detect_scaled_row_reuse only compares rows that live in multi-row bands of >=12
finite cells, and only accepts runs >=12 columns long. Real JCI "Supporting Data
Values" panels put each sub-panel on its own 1-4 row block and the copied/scaled
segment is short (3-8 columns) but HIGH-PRECISION — so an exact 7-column ratio of
0.8409 between two isolated single-row panels, or an 8-column byte-identical row
shared by two different genes, is invisible to the long-run detector. This detector
compares any two high-precision data rows and flags a short identical or constant-
ratio run, gated on >=5-significant-figure values so chance collisions stay
negligible. Signal, not verdict — neutral language.
"""
from __future__ import annotations

from paperconan import scan_dir
from paperconan._audit import detect_short_row_reuse
from paperconan._sheet import Sheet


# --- Real fixture values (verbatim from the JCI Supporting Data Values files) ---

# JCI201639 (HIF-2a) Supplemental Figure 2G / 2I: control block near-identical, the
# SOCS3sg1 block is Group_B = 0.8408979053 * Group_A over 7 columns.
P3_G = [1.099112, 0.989848, 0.990182, 0.961032, 1.05599, 1.059317, 0.902763, 0.94175,
        1.645786, 1.48695, 1.468761, 1.502732, 1.439502, 1.454987, 1.392053]
P3_I = [1.099114, 0.9898492, 0.990183, 0.961034, 1.055991, 1.059318, 0.902764, 0.941751,
        1.383938, 1.250373, 1.235078, 1.263644, 1.210474, 1.223495, 1.170574]

# JCI195506 (CHI3L1) Figure 3D / 3I TNF-a rows: 3I = 0.9361039404 * 3D over 3 columns.
P6_3D = [169.8665, 170.2768, 171.0974]
P6_3I = [159.0127, 159.3968, 160.1649]

# JCI182394 (KIF20A) Figure 2D Hs578T: SOX2 row and OCT4 row byte-identical (8 cols).
P7_SOX2 = [0.95705, 1.047294, 0.997692, 1.000679, 2.214018, 2.40605, 2.308038, 2.309369]
P7_OCT4 = [0.95705, 1.047294, 0.997692, 1.000679, 2.214018, 2.40605, 2.308038, 2.309369]


def _kinds(findings):
    return sorted(f["kind"] for f in findings)


def test_detects_short_constant_ratio_between_single_row_panels():
    # p3: two isolated single-row panels separated by header rows.
    sheet = Sheet.from_rows([
        ["Supplement", None, None, None],
        ["sgctrl", "SOCS3 sg1", "SOCS3 sg2", None],
        ["HIF1a", *P3_G],
        ["Supplement", None, None, None],
        ["sgctrl", "SOCS3 sg1", "SOCS3 sg2", None],
        ["HIF1a", *P3_I],
    ])
    findings = detect_short_row_reuse({("JCI201639.xlsx", "Supplement Figure 2"): sheet})
    scaled = [f for f in findings if f["kind"] == "scaled_row_reuse"]
    assert len(scaled) == 1, f"expected the 0.8409 ratio run, got {findings}"
    assert abs(scaled[0]["ratio"] - 0.8408979) < 1e-4 or abs(scaled[0]["ratio"] - 1 / 0.8408979) < 1e-4
    assert scaled[0]["run_length"] >= 7
    assert scaled[0]["severity"] == "high"


def test_detects_three_column_constant_ratio_high_precision():
    # p6: only a 3-column run, but 6-7 significant figures each → not chance.
    sheet = Sheet.from_rows([
        ["Figure 3D", "PBS", None, None],
        ["TNF-a", *P6_3D],
        ["Figure 3I", "Ctrl", None, None],
        ["TNF-a", *P6_3I],
    ])
    findings = detect_short_row_reuse({("JCI195506.xlsx", "Figure 3"): sheet})
    scaled = [f for f in findings if f["kind"] == "scaled_row_reuse"]
    assert len(scaled) == 1, f"expected a 3-col ratio finding, got {findings}"
    assert abs(scaled[0]["ratio"] - 0.9361039) < 1e-4 or abs(scaled[0]["ratio"] - 1 / 0.9361039) < 1e-4


def test_detects_short_identical_row_across_genes():
    # p7 Fig2D: SOX2 row == OCT4 row over 8 high-precision columns.
    sheet = Sheet.from_rows([
        ["NANOG", 1.032876, 0.983957, 0.983957, 1.000263, 2.244924, 2.260539, 2.183537, 2.229667],
        ["SOX2", *P7_SOX2],
        ["OCT4", *P7_OCT4],
        ["KIF20A", 0.959264, 0.97942, 1.06437, 1.001018, 4.947387, 4.336907, 5.01645, 4.766915],
    ])
    findings = detect_short_row_reuse({("JCI182394.xlsx", "Fig.2"): sheet})
    ident = [f for f in findings if f["kind"] == "identical_row_reuse"]
    assert len(ident) == 1, f"expected SOX2==OCT4 identical row, got {findings}"
    assert ident[0]["run_length"] == 8
    assert {ident[0]["row_a"], ident[0]["row_b"]} == {"SOX2", "OCT4"}


def test_no_false_positive_on_small_integer_counts():
    # Cell-count matrix (0/1/2): short identical runs are common by chance and low-info.
    sheet = Sheet.from_rows([
        ["a", 0.0, 1.0, 0.0, 2.0, 1.0, 0.0, 1.0],
        ["b", 0.0, 1.0, 0.0, 2.0, 0.0, 1.0, 0.0],
        ["c", 1.0, 1.0, 0.0, 2.0, 1.0, 0.0, 2.0],
    ])
    assert detect_short_row_reuse({("f.xlsx", "S1"): sheet}) == []


def test_no_false_positive_on_normalized_near_one_rows():
    # Rows normalized so every value ~1.0: an identical/ratio run would be <3 distinct.
    sheet = Sheet.from_rows([
        ["ctrl1", 1.001234, 0.998765, 1.000112, 0.999888, 1.002001],
        ["ctrl2", 1.001234, 0.998765, 1.000112, 0.999888, 1.002001],
    ])
    # These ARE identical and high-precision → this SHOULD fire (a real duplicate).
    # Guard the genuinely-benign case instead: only 2 distinct values.
    sheet2 = Sheet.from_rows([
        ["ctrl1", 1.0, 0.5, 1.0, 0.5, 1.0, 0.5],
        ["ctrl2", 1.0, 0.5, 1.0, 0.5, 1.0, 0.5],
    ])
    assert detect_short_row_reuse({("f.xlsx", "S2"): sheet2}) == []


def test_no_false_positive_on_two_value_overlap():
    # Only 2 aligned identical values (below the 3-column minimum) → no finding.
    sheet = Sheet.from_rows([
        ["a", 12.34567, 88.44231, 5.0, 6.0, 7.0],
        ["b", 12.34567, 88.44231, 9.0, 3.0, 1.0],
    ])
    assert detect_short_row_reuse({("f.xlsx", "S3"): sheet}) == []


def test_no_false_positive_on_quantized_grid():
    # k/19 body-weight normalization: every value is high-precision (7 sig figs) but drawn
    # from a tiny pool, so adjacent rows share 3 by chance. Must NOT fire (JCI182394 Fig11J).
    grid = [round(k / 19 * 100, 4) for k in range(13, 26)]   # 68.42..136.84, 13 distinct
    rows = []
    for r in range(12):
        rows.append([f"g{r}", *[grid[(r * 3 + c) % len(grid)] for c in range(8)]])
    sheet = Sheet.from_rows(rows)
    assert detect_short_row_reuse({("JCI182394.xlsx", "Fig.11"): sheet}) == []


def test_no_false_positive_on_large_integer_matrix():
    # Read counts / genomic coordinates / IDs: >=5-digit INTEGERS collide easily and carry
    # no fractional precision — a 3-column integer match must NOT fire (I1 regression).
    sheet = Sheet.from_rows([
        ["GeneA", 10234, 55012, 89341, 7, 3],
        ["GeneB", 10234, 55012, 89341, 2, 9],
        ["GeneC", 12345, 67890, 24680, 1, 5],
    ])
    assert detect_short_row_reuse({("counts.xlsx", "S1"): sheet}) == []


def test_power_of_ten_unit_conversion_with_precision_mismatch_not_flagged():
    # x100 restatement where the two panels are stored at different decimal precision, so the
    # mean ratio lands ~1e-5 off exactly 100. Must still be recognized as a benign unit
    # conversion, not surfaced as a bare high finding (I2 regression).
    a = [12.345678, 56.789134, 34.567812, 78.912345, 23.456789]  # 6 dp
    b = [round(v * 100, 3) for v in a]      # a*100 re-stored to 3 dp -> ratio drifts ~2e-5
    # header rows keep the two panels in DIFFERENT bands (so same-band suppression is not
    # what hides it — this must be caught as a power-of-ten conversion).
    sheet = Sheet.from_rows([["p1", "", "", "", "", ""], ["x", *a],
                             ["p2", "", "", "", "", ""], ["y", *b]])
    scaled = [f for f in detect_short_row_reuse({("f.xlsx", "S4"): sheet})
              if f["kind"] == "scaled_row_reuse"]
    assert scaled == [], f"power-of-ten unit conversion should be skipped, got {scaled}"


def test_power_of_ten_ratio_is_not_flagged():
    # An exact x10 restatement (unit change) is benign, not a data-inconsistency signal.
    base = [3.141592, 2.718281, 1.414213, 1.732050, 2.236067]
    sheet = Sheet.from_rows([["p1", "", "", "", "", ""], ["a", *base],
                             ["p2", "", "", "", "", ""], ["b", *[v * 10 for v in base]]])
    scaled = [f for f in detect_short_row_reuse({("f.xlsx", "S5"): sheet})
              if f["kind"] == "scaled_row_reuse"]
    assert scaled == []


def test_distinct_same_label_pairs_are_not_collapsed(tmp_path):
    # Three isolated single-row panels labelled "M", panel2 and panel3 each == panel1. The
    # A-B and A-C pairs are DISTINCT row pairs that share the label "M"; neither may be
    # dropped by a label-keyed dedup (I3 regression).
    v = [0.8791397, 1.1921357, 0.9541495, 1.0233217]
    rows = [
        ["hdr", "", "", "", ""], ["M", *v],
        ["hdr", "", "", "", ""], ["M", *v],
        ["hdr", "", "", "", ""], ["M", *v],
    ]
    data = tmp_path / "data"
    data.mkdir()
    (data / "s.csv").write_text(
        "\n".join(",".join("" if x == "" else str(x) for x in r) for r in rows) + "\n",
        encoding="utf-8")
    res = scan_dir(str(data), str(tmp_path / "out"), write_html=False)
    ident = [f for f in res.get("cross_sheet_findings", []) or []
             if f.get("short_run") and f["kind"] == "identical_row_reuse"]
    assert len(ident) >= 2, f"distinct same-label pairs were collapsed: {ident}"


def test_same_label_unit_scaled_row_is_not_downgraded_as_derived():
    # A row labelled with a unit ("TNF-α (pg/mL)") scaled by an arbitrary constant to a
    # SAME-labelled row in another panel is a duplicate, not a unit conversion (a conversion
    # relabels the row). The derived-relation heuristic must not downgrade it (JCI195506
    # Fig 3D/3I: Group B = 0.9361 * Group A stayed high only after this fix).
    from paperconan._profiles import _is_derived_relation
    same = {"kind": "scaled_row_reuse", "row_a": "TNF-α (pg/mL)", "row_b": "TNF-α (pg/mL)"}
    assert not _is_derived_relation(same)
    # DIFFERENT-labelled rows with a unit token are still a plausible derivation.
    diff = {"kind": "scaled_row_reuse", "row_a": "conc (ng/mL)", "row_b": "conc (µg/mL)"}
    assert _is_derived_relation(diff)


def test_p6_like_cytokine_ratio_reaches_review_high(tmp_path):
    from paperconan.packet import distill_findings_for_review
    rows = [
        ["Figure 3D", "PBS", "", ""],
        ["TNF-α (pg/mL)", *P6_3D],
        ["Figure 3I", "Ctrl", "", ""],
        ["TNF-α (pg/mL)", *P6_3I],
    ]
    data = tmp_path / "data"
    data.mkdir()
    (data / "s.csv").write_text(
        "\n".join(",".join("" if v == "" else str(v) for v in r) for r in rows) + "\n",
        encoding="utf-8")
    res = scan_dir(str(data), str(tmp_path / "out"), write_html=False)
    high = [f for f in res.get("cross_sheet_findings", []) or []
            if f.get("short_run") and str(f.get("severity")).lower() == "high"]
    assert high, "same-label cytokine scaled row was wrongly downgraded below high"
    distilled = distill_findings_for_review(res)
    assert any(str(d.get("kind", "")).startswith("cross_sheet:scaled_row") for d in distilled)


def test_scan_dir_surfaces_short_row_reuse(tmp_path):
    rows = [
        ["Supplement", "", "", ""],
        ["sgctrl", "SOCS3 sg1", "SOCS3 sg2", ""],
        ["HIF1a", *P3_G],
        ["Supplement", "", "", ""],
        ["sgctrl", "SOCS3 sg1", "SOCS3 sg2", ""],
        ["HIF1a", *P3_I],
    ]
    data = tmp_path / "data"
    data.mkdir()
    (data / "s.csv").write_text(
        "\n".join(",".join("" if v == "" else str(v) for v in r) for r in rows) + "\n",
        encoding="utf-8")
    res = scan_dir(str(data), str(tmp_path / "out"), write_html=False)
    short = [f for f in res.get("cross_sheet_findings", []) or []
             if f.get("kind") in ("scaled_row_reuse", "identical_row_reuse")]
    assert short, "scan_dir did not surface the short row reuse"
