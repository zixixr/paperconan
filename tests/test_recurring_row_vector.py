"""B2: detect_recurring_row_vectors — a fixed, high-information numeric tuple recurring as a
contiguous row-slice across >=3 places spanning >=2 figure namespaces. Includes a brute-force
oracle and the FP guards (patterned/ladder tuples, single-figure recurrence, too few occurrences)."""
from __future__ import annotations

from paperconan._audit import (
    Sheet,
    detect_recurring_row_vectors,
    figure_key,
    find_numeric_blocks,
)
from paperconan._summaries import RecurringRowIndex

_W = 9   # uniform row width so a numeric block covers the full vector


def _sheet(rows):
    return Sheet.from_rows([[f"c{j}" for j in range(_W)]] + [list(r) for r in rows])


def _pad(seed, k):
    """k filler values unique to a sheet (seed), so padding never recurs across sheets."""
    return [round(seed * 1.7 + 0.31 * j + 0.07, 4) for j in range(k)]


def _row(vec, seed):
    return list(vec) + _pad(seed, _W - len(vec))


def _fill(seed):
    """Three DISTINCT full-width filler rows unique to a sheet."""
    return [[round(seed + 0.13 * i + 0.7 * j, 4) for j in range(_W)] for i in range(1, 4)]


def _panel(vec, seed):
    return [_row(vec, seed)] + _fill(seed)


def _b2_oracle(panels, vec, min_occ=3, min_ns=2):
    """Independent ground truth: distinct (sheet,row) sites where `vec` appears as a contiguous
    row-slice, and the figure namespaces they span."""
    sites, ns = set(), set()
    for (f, s), rows in panels.items():
        for ri, row in enumerate(rows):
            nums = [float(x) if isinstance(x, (int, float)) and not isinstance(x, bool) else None for x in row]
            for start in range(len(nums) - len(vec) + 1):
                win = nums[start:start + len(vec)]
                if all(w is not None for w in win) and [round(w, 6) for w in win] == [round(v, 6) for v in vec]:
                    sites.add((f, s, ri))
                    if figure_key(s):
                        ns.add(figure_key(s))
    return len(sites) >= min_occ and len(ns) >= min_ns


VEC = [220.0, 188.0, 122.0, 166.0, 128.0, 166.0]     # high-information, not a ladder
WIDE_VEC = [
    coefficient * 2**53 + offset
    for coefficient, offset in (
        (1, 0),
        (3, 17),
        (2, 3),
        (5, 21),
        (4, 8),
        (7, 14),
    )
]


def test_b2_flags_recurring_vector_across_figures_and_matches_oracle():
    panels = {
        ("M1.xls", "Figure 1e"): _panel(VEC, 10),
        ("M2.xls", "Figure 4b"): _panel(VEC, 40),
        ("M3.xls", "Extended Data Fig. 2a"): _panel(VEC, 70),
    }
    f = detect_recurring_row_vectors({k: _sheet(v) for k, v in panels.items()})
    hi = [x for x in f if x["kind"] == "recurring_row_vector" and x["severity"] == "high"]
    assert len(hi) == 1, [x["vector"] for x in hi]
    assert hi[0]["vector"] == VEC
    assert hi[0]["n_occurrences"] == 3 and hi[0]["n_figures"] >= 2
    assert _b2_oracle(panels, VEC) is True


def test_b2_no_flag_on_arithmetic_ladder():
    ladder = [10.0, 20.0, 30.0, 40.0, 50.0, 60.0]
    panels = {
        ("M1.xls", "Figure 1e"): _panel(ladder, 10),
        ("M2.xls", "Figure 4b"): _panel(ladder, 40),
        ("M3.xls", "Extended Data Fig. 2a"): _panel(ladder, 70),
    }
    f = detect_recurring_row_vectors({k: _sheet(v) for k, v in panels.items()})
    assert not [x for x in f if x["severity"] == "high"]


def test_b2_no_flag_when_single_figure_namespace():
    # recurrence confined to one figure (main:4, panels 4b/4c/4d) is expected replicate structure
    panels = {
        ("M.xls", "Figure 4b"): _panel(VEC, 10),
        ("M.xls", "Figure 4c"): _panel(VEC, 40),
        ("M.xls", "Figure 4d"): _panel(VEC, 70),
    }
    f = detect_recurring_row_vectors({k: _sheet(v) for k, v in panels.items()})
    assert not [x for x in f if x["severity"] == "high"]
    assert _b2_oracle(panels, VEC) is False


def test_b2_no_flag_on_two_occurrences_only():
    panels = {
        ("M1.xls", "Figure 1e"): _panel(VEC, 10),
        ("M2.xls", "Figure 4b"): _panel(VEC, 40),
    }
    f = detect_recurring_row_vectors({k: _sheet(v) for k, v in panels.items()})
    assert not [x for x in f if x["severity"] == "high"]
    assert _b2_oracle(panels, VEC) is False


def test_b2_no_flag_on_near_constant_vector():
    const = [5.0, 5.0, 5.0, 5.0, 5.0, 5.0]
    panels = {
        ("M1.xls", "Figure 1e"): _panel(const, 10),
        ("M2.xls", "Figure 4b"): _panel(const, 40),
        ("M3.xls", "Extended Data Fig. 2a"): _panel(const, 70),
    }
    f = detect_recurring_row_vectors({k: _sheet(v) for k, v in panels.items()})
    assert not [x for x in f if x["severity"] == "high"]


def test_b2_dedups_overlapping_windows_to_one_finding():
    # a long recurring row-run yields many overlapping k=4..8 windows → must report once
    run = [220.0, 188.0, 122.0, 166.0, 128.0, 166.0, 199.0, 254.0]
    panels = {
        ("M1.xls", "Figure 1e"): _panel(run, 10),
        ("M2.xls", "Figure 4b"): _panel(run, 40),
        ("M3.xls", "Extended Data Fig. 2a"): _panel(run, 70),
    }
    f = detect_recurring_row_vectors({k: _sheet(v) for k, v in panels.items()})
    hi = [x for x in f if x["severity"] == "high"]
    assert len(hi) == 1, [x["vector"] for x in hi]


def test_b2_single_namespace_early_exit():
    # a corpus that can never reach >=2 figure namespaces must skip the whole expensive pass
    panels = {("M.xls", "Sheet1"): _panel(VEC, 10), ("M.xls", "Data"): _panel(VEC, 40)}
    assert detect_recurring_row_vectors({k: _sheet(v) for k, v in panels.items()}) == []


def test_b2_same_sheet_name_across_files_not_conflated():
    # regression: the dedup 'cells' key omitted the file, so two files sharing a sheet name
    # ('Sheet1') with vectors at overlapping positions could be merged. Two DISTINCT vectors
    # each recurring across >=2 real figures must both survive.
    v1 = [220.0, 188.0, 122.0, 166.0, 128.0, 166.0]
    v2 = [311.0, 277.0, 203.0, 255.0, 199.0, 241.0]
    panels = {
        ("A.xls", "Sheet1"): _panel(v1, 10),                 # figure_key None
        ("A.xls", "Figure 4b"): _panel(v1, 40),              # main:4
        ("A.xls", "Extended Data Fig. 2a"): _panel(v1, 70),  # ext:2
        ("B.xls", "Sheet1"): _panel(v2, 11),                 # same sheet name, different file/vector
        ("B.xls", "Figure 5b"): _panel(v2, 41),              # main:5
        ("B.xls", "Extended Data Fig. 3a"): _panel(v2, 71),  # ext:3
    }
    f = detect_recurring_row_vectors({k: _sheet(v) for k, v in panels.items()})
    vecs = {tuple(x["vector"]) for x in f if x["severity"] == "high"}
    assert tuple(v1) in vecs and tuple(v2) in vecs, f"both distinct vectors must survive: {vecs}"


def test_b2_incremental_index_matches_compatibility_wrapper():
    panels = {
        ("M1.xls", "Figure 1e"): _panel(VEC, 10),
        ("M2.xls", "Figure 4b"): _panel(VEC, 40),
        ("M3.xls", "Extended Data Fig. 2a"): _panel(VEC, 70),
    }
    sheets = {key: _sheet(rows) for key, rows in panels.items()}
    index = RecurringRowIndex()
    for (file, name), source in sheets.items():
        index.add_sheet(
            file,
            name,
            source,
            blocks=find_numeric_blocks(source),
            figure_id=figure_key(name),
        )

    compact, meta = index.findings()

    assert compact == detect_recurring_row_vectors(sheets)
    assert meta == {"findings_omitted": 0}


def test_b2_site_count_survives_bounded_site_evidence():
    panels = {
        (f"M{i:02d}.xls", f"Figure {i + 1}a"): _panel(VEC, i * 10 + 1)
        for i in range(20)
    }
    index = RecurringRowIndex()
    for (file, name), rows in panels.items():
        source = _sheet(rows)
        index.add_sheet(
            file,
            name,
            source,
            blocks=find_numeric_blocks(source),
            figure_id=figure_key(name),
        )

    findings, meta = index.findings()
    match = next(finding for finding in findings if finding["vector"] == VEC)

    assert match["n_occurrences"] == 20
    assert meta == {"findings_omitted": 0}


def test_b2_repeated_start_columns_in_one_row_count_as_one_occurrence():
    index = RecurringRowIndex()
    source_a = Sheet.from_rows([VEC + VEC])
    source_b = Sheet.from_rows([VEC])
    index.add_sheet(
        "M1.xls",
        "Figure 1a",
        source_a,
        blocks=[(0, 1, 0, source_a.ncols)],
        figure_id="main:1",
        min_k=6,
        max_k=6,
    )
    index.add_sheet(
        "M2.xls",
        "Figure 2a",
        source_b,
        blocks=[(0, 1, 0, source_b.ncols)],
        figure_id="main:2",
        min_k=6,
        max_k=6,
    )

    findings, meta = index.findings()

    assert not any(finding["vector"] == VEC for finding in findings)
    assert meta == {"findings_omitted": 0}


def test_b2_location_metadata_includes_occurrences_after_site_cap():
    index = RecurringRowIndex()
    for number in range(100, 116):
        source = Sheet.from_rows([VEC])
        index.add_sheet(
            "A.xls",
            f"Figure {number:03d}a",
            source,
            blocks=[(0, 1, 0, source.ncols)],
            figure_id=f"main:{number}",
            min_k=6,
            max_k=6,
        )
    source = Sheet.from_rows([VEC])
    index.add_sheet(
        "Z.xls",
        "Figure 002a",
        source,
        blocks=[(0, 1, 0, source.ncols)],
        figure_id="main:2",
        min_k=6,
        max_k=6,
    )

    findings, _meta = index.findings()
    finding = next(item for item in findings if item["vector"] == VEC)

    assert finding["n_occurrences"] == 17
    assert finding["same_file"] is False
    assert finding["file_a"] == "A.xls"
    assert finding["file_b"] == "Z.xls"
    assert "Z.xls" in finding["file"]
    assert finding["sheet_a"] == "Figure 002a"
    assert finding["sheet_b"] == "Figure 115a"
    assert "Figure 002a" in finding["sheet"]
    assert "Figure 002a" in finding["rule"]


def test_b2_findings_reports_exact_truncation_metadata():
    index = RecurringRowIndex()
    vector_b = [311.0, 277.0, 203.0, 255.0, 199.0, 241.0]
    for number in range(1, 4):
        source = Sheet.from_rows([VEC, vector_b])
        index.add_sheet(
            f"M{number}.xls",
            f"Figure {number}a",
            source,
            blocks=[(0, 2, 0, source.ncols)],
            figure_id=f"main:{number}",
            min_k=6,
            max_k=6,
        )

    findings, meta = index.findings(max_findings=1)

    assert len(findings) == 1
    assert meta == {"findings_omitted": 1}


def test_recurring_vector_preserves_identical_wide_integers_in_evidence():
    panels = {
        ("M1.xlsx", "Figure 1a"): _panel(WIDE_VEC, 10),
        ("M2.xlsx", "Figure 2a"): _panel(WIDE_VEC, 40),
        ("M3.xlsx", "Figure 3a"): _panel(WIDE_VEC, 70),
    }

    findings = detect_recurring_row_vectors({
        key: _sheet(rows) for key, rows in panels.items()
    })
    match = next(
        finding for finding in findings
        if finding["vector"] == WIDE_VEC
    )

    assert all(isinstance(value, int) for value in match["vector"])
    assert [example["value"] for example in match["examples"]] == WIDE_VEC
    assert all(
        isinstance(example["value"], int)
        for example in match["examples"]
    )


def test_distinct_wide_integer_vectors_do_not_collapse_into_recurrence():
    base = [coefficient * 2**55 for coefficient in (1, 3, 2, 5, 4, 7)]
    panels = {
        ("M1.xlsx", "Figure 1a"): _panel(base, 10),
        ("M2.xlsx", "Figure 2a"): _panel([value + 1 for value in base], 40),
        ("M3.xlsx", "Figure 3a"): _panel([value + 2 for value in base], 70),
    }

    findings = detect_recurring_row_vectors({
        key: _sheet(rows) for key, rows in panels.items()
    })

    assert findings == []
