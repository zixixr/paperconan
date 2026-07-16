"""Row-oriented relation detector (transpose-aware constant ratio / identical rows).

The column-pair `detect_relations` is blind to source-data laid out with
experimental CONDITIONS in rows and per-cell MEASUREMENTS in columns: there a
"row B = row A * k" relationship (two different conditions that are an exact
scalar multiple of each other across many columns) never touches a column pair.
`detect_row_relations` closes that gap. Signal, not verdict — neutral language.
"""
from __future__ import annotations

import paperconan._audit as _audit
from paperconan import scan_dir
from paperconan._audit import benign_reason, detect_row_relations
from paperconan._profiles import apply_profile_to_findings
from paperconan._sheet import Sheet


def _distinct_highprec(n, a, b):
    """Deterministic, distinct, non-integer, non-zero values (no repeats, no seed=Math.random)."""
    return [13.0 + ((k * a + b) % 97) + (k + 1) * 0.6180339887 for k in range(n)]


def test_detects_constant_ratio_between_condition_rows():
    base = _distinct_highprec(30, 31, 7)
    scaled = [v * 1.14 for v in base]              # a different condition = base * 1.14, cell for cell
    unrelated = _distinct_highprec(30, 53, 17)
    header = ["cond", *[f"m{i}" for i in range(30)]]
    rows = [
        header,
        ["shControl", *unrelated],
        ["shUSP15-2+shPARP1-2", *base],
        ["shUSP15-2+pPARP1", *scaled],
    ]
    sheet = Sheet.from_rows(rows)

    findings = detect_row_relations(sheet, 1, 4, 1, 31, header[1:])

    ratio = [f for f in findings if f["kind"] == "constant_ratio_row"]
    assert len(ratio) == 1, f"expected exactly one row-ratio finding, got {findings}"
    f = ratio[0]
    assert {f["row_a"], f["row_b"]} == {"shUSP15-2+shPARP1-2", "shUSP15-2+pPARP1"}
    assert abs(f["ratio"] - 1.14) < 1e-9 or abs(f["ratio"] - 1 / 1.14) < 1e-9
    assert f["n"] == 30
    assert f["severity"] == "high"


def test_detects_partial_contiguous_ratio_run():
    # The real fingerprint: the ratio holds over a CONTIGUOUS run of columns (the first
    # 40 here), while the rest of the row diverges — a copy-then-scale of part of a row.
    base = _distinct_highprec(60, 31, 7)
    other = _distinct_highprec(60, 53, 17)
    row_b = [base[k] * 1.14 if k < 40 else other[k] for k in range(60)]
    header = ["cond", *[f"m{i}" for i in range(60)]]
    rows = [
        header,
        ["shUSP15-2+shPARP1-2", *base],
        ["shUSP15-2+pPARP1", *row_b],
    ]
    sheet = Sheet.from_rows(rows)

    findings = detect_row_relations(sheet, 1, 3, 1, 61, header[1:])

    ratio = [f for f in findings if f["kind"] == "constant_ratio_row"]
    assert len(ratio) == 1, f"expected one partial row-ratio finding, got {findings}"
    f = ratio[0]
    assert abs(f["ratio"] - 1.14) < 1e-9 or abs(f["ratio"] - 1 / 1.14) < 1e-9
    assert f["run_length"] == 40
    assert f["severity"] == "high"


def test_ratio_run_survives_low_precision_2_decimals():
    # Source data stored to 2 decimals still reads back as a constant ratio (rounding
    # noise ~1e-4 relative); the run must survive, not collapse. Regression for a
    # too-tight membership tolerance.
    base = [round(50.0 + ((k * 37 + 11) % 40) + (k + 1) * 0.271, 2) for k in range(40)]
    scaled = [round(v * 1.14, 2) for v in base]
    header = ["cond", *[f"m{i}" for i in range(40)]]
    rows = [header, ["A", *base], ["B", *scaled]]
    sheet = Sheet.from_rows(rows)

    findings = detect_row_relations(sheet, 1, 3, 1, 41, header[1:])
    ratio = [f for f in findings if f["kind"] == "constant_ratio_row"]
    assert ratio and ratio[0]["run_length"] >= 30, f"low-precision ratio run collapsed: {findings}"


def test_scaled_run_not_masked_by_identical_prefix():
    # A pair identical over a long prefix and cleanly scaled over the suffix must still
    # surface the scaling — the near-unity prefix must not win and abort detection.
    base = _distinct_highprec(100, 31, 7)
    row_b = [base[k] if k < 50 else base[k] * 1.05 for k in range(100)]
    header = ["cond", *[f"m{i}" for i in range(100)]]
    sheet = Sheet.from_rows([header, ["A", *base], ["B", *row_b]])

    findings = detect_row_relations(sheet, 1, 3, 1, 101, header[1:])
    ratio = [f for f in findings if f["kind"] == "constant_ratio_row"]
    assert ratio, f"scaled suffix masked by identical prefix: {findings}"
    assert abs(ratio[0]["ratio"] - 1.05) < 1e-6 or abs(ratio[0]["ratio"] - 1 / 1.05) < 1e-6
    assert ratio[0]["run_length"] == 50


def test_named_unit_conversion_rows_are_demoted():
    # Two rows that are a named unit conversion (kg vs lb, ratio ~2.2046) must not stay
    # a bare HIGH — the derived-relation prefilter should demote them under review.
    f = {"kind": "constant_ratio_row", "ratio": 2.2046, "severity": "high",
         "row_a": "Weight (kg)", "row_b": "Weight (lb)", "n": 30}
    apply_profile_to_findings([f], "review")
    assert f["profile_action"] in ("demoted", "hidden") or str(f["severity"]).lower() == "low"


def test_row_relations_bounded_by_budget(monkeypatch):
    base = _distinct_highprec(40, 31, 7)
    header = ["cond", *[f"m{i}" for i in range(40)]]
    sheet = Sheet.from_rows([header, ["A", *base], ["B", *[v * 1.14 for v in base]]])
    # default budget: the ratio pair is found
    assert detect_row_relations(sheet, 1, 3, 1, 41, header[1:])
    # a starved budget stops before doing the per-pair column scan (cost bound)
    monkeypatch.setattr(_audit, "_ROW_REL_BUDGET", 1)
    assert detect_row_relations(sheet, 1, 3, 1, 41, header[1:]) == []


def test_no_false_positive_on_independent_rows():
    rows = [
        ["cond", *[f"m{i}" for i in range(30)]],
        ["A", *_distinct_highprec(30, 31, 7)],
        ["B", *_distinct_highprec(30, 53, 17)],
        ["C", *_distinct_highprec(30, 71, 41)],
    ]
    sheet = Sheet.from_rows(rows)

    assert detect_row_relations(sheet, 1, 4, 1, 31, rows[0][1:]) == []


def test_detects_identical_condition_rows():
    vals = _distinct_highprec(24, 31, 7)
    rows = [
        ["cond", *[f"m{i}" for i in range(24)]],
        ["Repeat1 / cohort X", *vals],
        ["other", *_distinct_highprec(24, 53, 17)],
        ["Repeat3 / cohort Y", *vals],          # a bit-identical data group under a different label
    ]
    sheet = Sheet.from_rows(rows)

    findings = detect_row_relations(sheet, 1, 4, 1, 25, rows[0][1:])

    ident = [f for f in findings if f["kind"] == "identical_row"]
    assert len(ident) == 1
    assert {ident[0]["row_a"], ident[0]["row_b"]} == {"Repeat1 / cohort X", "Repeat3 / cohort Y"}
    assert ident[0]["severity"] == "high"


def test_skips_narrow_block_below_min_cols():
    # A proportional pair over too few columns is not distinctive — must not fire.
    base = _distinct_highprec(6, 31, 7)
    rows = [
        ["cond", *[f"m{i}" for i in range(6)]],
        ["A", *base],
        ["B", *[v * 1.14 for v in base]],
    ]
    sheet = Sheet.from_rows(rows)

    assert detect_row_relations(sheet, 1, 3, 1, 7, rows[0][1:]) == []


def test_skips_block_with_too_many_rows():
    # Guard against O(rows^2) on tall entity-in-rows tables: a huge block is skipped
    # even if it hides a proportional pair.
    n = 200
    base = _distinct_highprec(20, 31, 7)
    rows = [["cond", *[f"m{i}" for i in range(20)]]]
    for r in range(n):
        rows.append([f"e{r}", *_distinct_highprec(20, 7 * r + 3, r + 1)])
    rows[1] = ["A", *base]
    rows[2] = ["B", *[v * 1.14 for v in base]]
    sheet = Sheet.from_rows(rows)

    assert detect_row_relations(sheet, 1, n + 1, 1, 21, rows[0][1:]) == []


def test_round_power_of_ten_ratio_is_flagged_likely_benign():
    # ratio == 100 across a row pair is the classic percent-vs-fraction / unit conversion —
    # a derived relation, not a copied measurement. benign_reason must say so.
    f = {"kind": "constant_ratio_row", "ratio": 100.0,
         "row_a": "value", "row_b": "value (%)"}
    assert benign_reason(f)  # non-empty innocent explanation

    # an arbitrary ratio (1.14) has no such innocent transform — stays unexplained.
    f2 = {"kind": "constant_ratio_row", "ratio": 1.14,
          "row_a": "shUSP15-2+shPARP1-2", "row_b": "shUSP15-2+pPARP1"}
    assert not benign_reason(f2)


def test_row_findings_distill_with_usable_location(tmp_path):
    # The paperconan-watch review packet must not drop the row finding's location and
    # value samples just because it names rows (row_a/row_b), not columns.
    from paperconan.packet import distill_findings_for_review
    base = _distinct_highprec(30, 31, 7)
    header = ["cond", *[f"m{i}" for i in range(30)]]
    rows = [header, ["Control", *_distinct_highprec(30, 53, 17)],
            ["USP15 KO-1", *base], ["PARP1 KO-1", *[v * 1.042 for v in base]]]
    data = tmp_path / "data"
    data.mkdir()
    (data / "s.csv").write_text(
        "\n".join(",".join(str(v) for v in row) for row in rows) + "\n", encoding="utf-8")
    scan = scan_dir(str(data), str(tmp_path / "out"), write_html=False)

    distilled = distill_findings_for_review(scan)
    rr = [d for d in distilled if d.get("kind") == "constant_ratio_row"]
    assert rr, "row finding was dropped from the review packet"
    assert rr[0].get("col_a"), "distilled row finding lost its location"
    assert rr[0].get("top5_a"), "distilled row finding lost its value samples"


def test_scan_dir_surfaces_row_relations_and_html(tmp_path):
    base = _distinct_highprec(30, 31, 7)
    header = ["cond", *[f"m{i}" for i in range(30)]]
    rows = [
        header,
        ["Control", *_distinct_highprec(30, 53, 17)],
        ["USP15 KO-1", *base],
        ["PARP1 KO-1", *[v * 1.042 for v in base]],
    ]
    data = tmp_path / "data"
    data.mkdir()
    (data / "source.csv").write_text(
        "\n".join(",".join(str(v) for v in row) for row in rows) + "\n",
        encoding="utf-8",
    )

    res = scan_dir(str(data), str(tmp_path / "out"), write_html=True)

    row_rel = [
        f
        for blk in res.get("relations_blocks", []) or []
        for f in blk.get("row_relations", []) or []
    ]
    assert any(f["kind"] == "constant_ratio_row" for f in row_rel)
    html = (tmp_path / "out" / "report.html").read_text(encoding="utf-8")
    assert "constant_ratio_row" in html
