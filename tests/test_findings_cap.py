"""Output-size guard: a single dense, highly-correlated block must not emit an
unbounded number of findings.

Regression for GitHub issue #15: a modestly sized but dense sheet (many mutually
proportional columns) makes the O(col^2) pairwise detectors emit thousands of
findings, each carrying an embedded evidence snippet, so scan.json / report.html
balloon to > 1 GB and the browser cannot open them. scan_dir must cap the number
of findings retained per block (keeping the highest-severity ones) and record how
many were omitted, so the report stays bounded and honest about the truncation.
"""
from __future__ import annotations

import csv

import openpyxl

import paperconan._audit as A
from paperconan._audit import (
    BLOCK_FINDING_GROUPS,
    _MAX_FINDINGS_PER_BLOCK,
    _cap_block_findings,
    scan_dir,
)
from paperconan._resources import BoundedFindingCollector


def _write_dense_csv(path, n_rows=40, n_cols=60):
    """A block where every column is a fixed scalar multiple of the first, so the
    linear/ratio/equal-pair detectors fire on ~every one of the O(col^2) pairs."""
    base = [round(1.0 + i * 0.7, 4) for i in range(n_rows)]
    header = [f"c{c}" for c in range(n_cols)]
    rows = []
    for r in range(n_rows):
        rows.append([round(base[r] * (c + 1), 4) for c in range(n_cols)])
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(header)
        w.writerows(rows)


def _block_finding_count(blk):
    return sum(len(blk.get(g) or []) for g in BLOCK_FINDING_GROUPS)


def _write_two_sheet_blocks(path):
    wb = openpyxl.Workbook()
    first = wb.active
    first.title = "First"
    second = wb.create_sheet("Second")
    for ws, base in ((first, 0), (second, 1000)):
        ws.append(["a", "b"])
        for i in range(5):
            ws.append([base + i + 1, base + i + 11])
        ws.append([None, None])
        for i in range(5):
            ws.append([base + i + 101, base + i + 121])
    wb.save(path)


def test_dense_block_findings_are_capped(tmp_path, monkeypatch):
    data = tmp_path / "dense"
    data.mkdir()
    _write_dense_csv(str(data / "dense.csv"))
    monkeypatch.setattr(A, "_MAX_TOTAL_FINDINGS", 0)

    scan = scan_dir(str(data), str(tmp_path / "out"), write_html=False)

    blocks = scan.get("relations_blocks") or []
    assert blocks, "expected the dense block to produce findings"
    for blk in blocks:
        n = _block_finding_count(blk)
        assert n <= _MAX_FINDINGS_PER_BLOCK, (
            f"block {blk['file']}::{blk['sheet']} kept {n} findings, "
            f"exceeds cap {_MAX_FINDINGS_PER_BLOCK}"
        )

    # The truncation must be recorded, not silent: this dense fixture generates far
    # more than the cap, so at least one block reports omitted findings.
    total_omitted = sum(int(blk.get("findings_omitted") or 0) for blk in blocks)
    assert total_omitted > 0, "dense block exceeded the cap but omission was not recorded"
    assert scan["scan_status"] == "partial"
    finding_limits = [
        item
        for item in scan["coverage"]["limitations"]
        if item["reason"] == "finding_limit"
    ]
    assert any(
        item["reason"] == "finding_limit"
        and item.get("omitted_findings", 0) > 0
        for item in finding_limits
    )
    assert scan["coverage"]["blocks_analyzed"] == 1
    assert scan["coverage"]["blocks_skipped"] == 0
    assert all(item["scope"] == "block" for item in finding_limits)


def test_report_block_cap_counts_remaining_blocks_once_per_sheet(
    tmp_path, monkeypatch
):
    data = tmp_path / "blocks"
    data.mkdir()
    _write_two_sheet_blocks(data / "multi.xlsx")
    monkeypatch.setattr(A, "_MAX_REPORT_BLOCKS", 1)
    monkeypatch.setattr(A, "_MAX_TOTAL_FINDINGS", 0)

    scan = scan_dir(str(data), str(tmp_path / "out"), write_html=False)

    assert scan["scan_status"] == "partial"
    assert scan["coverage"]["blocks_analyzed"] == 1
    assert scan["coverage"]["blocks_skipped"] == 3
    assert scan["coverage"]["limitations"] == [
        {
            "scope": "sheet",
            "reason": "report_block_limit",
            "count": 1,
            "file": "multi.xlsx",
            "sheet": "First",
        },
        {
            "scope": "sheet",
            "reason": "report_block_limit",
            "count": 2,
            "file": "multi.xlsx",
            "sheet": "Second",
        },
    ]


def test_global_finding_cap_counts_omissions_without_duplicates(
    tmp_path, monkeypatch
):
    data = tmp_path / "blocks"
    data.mkdir()
    _write_two_sheet_blocks(data / "multi.xlsx")
    monkeypatch.setattr(A, "_MAX_REPORT_BLOCKS", 100)
    monkeypatch.setattr(A, "_MAX_TOTAL_FINDINGS", 1)
    monkeypatch.setattr(A, "_MAX_FINDINGS_PER_BLOCK", 0)

    scan = scan_dir(str(data), str(tmp_path / "out"), write_html=False)

    coverage = scan["coverage"]
    assert scan["scan_status"] == "partial"
    assert coverage["blocks_analyzed"] == 4
    assert coverage["blocks_skipped"] == 0
    assert coverage["limitations"] == [
        {
            "scope": "scan",
            "reason": "global_finding_limit",
            "limit": 1,
            "omitted_findings": 11,
        }
    ]
    assert scan["findings_omitted"] == 11


def test_cap_keeps_highest_severity_first():
    """`_cap_block_findings` must drop the LEAST-severe findings first, so no dropped
    finding outranks a kept one. Tested directly on the helper because the e2e path runs
    `_demote_dense_sheets` afterwards, which flattens a dense sheet's severities to 'low'
    and would mask whether the cap itself selected by severity."""
    groups = {
        "relations": [{"severity": "low", "i": i} for i in range(100)]
                     + [{"severity": "high", "i": i} for i in range(20)],
        "grim": [{"severity": "medium", "i": i} for i in range(30)],
    }
    omitted = _cap_block_findings(groups, 40)

    kept = [f for lst in groups.values() for f in lst]
    assert len(kept) == 40
    assert omitted == 110
    counts = {s: sum(1 for f in kept if f["severity"] == s) for s in ("high", "medium", "low")}
    # 20 high + 30 medium = 50 > cap 40, so all 40 kept are high/medium and NO low survives.
    assert counts == {"high": 20, "medium": 20, "low": 0}, counts


def test_cap_is_deterministic_and_stable():
    """Ties within a severity band keep original order, so two identical inputs cap to the
    same findings (the scan output must stay byte-identical across runs)."""
    def fresh():
        return {"relations": [{"severity": "high", "i": i} for i in range(10)],
                "grim": [{"severity": "high", "i": i} for i in range(10)]}
    a, b = fresh(), fresh()
    _cap_block_findings(a, 5)
    _cap_block_findings(b, 5)
    assert a == b
    # Stable = the first-emitted findings win the ties.
    assert [f["i"] for f in a["relations"]] == [0, 1, 2, 3, 4]
    assert a["grim"] == []


def test_cap_none_is_unlimited():
    groups = {"relations": [{"severity": "low"}] * 500}
    assert _cap_block_findings(groups, None) == 0
    assert len(groups["relations"]) == 500


def test_within_sheet_findings_consume_shared_pre_cap_budget_before_return(
    monkeypatch,
):
    markers = [
        {
            "kind": f"within_{index}",
            "severity": "high",
            "rule": f"marker {index}",
        }
        for index in range(3)
    ]

    monkeypatch.setattr(
        A,
        "_analyze_numeric_blocks",
        lambda *_args, **_kwargs: [],
    )
    monkeypatch.setattr(
        A,
        "detect_within_sheet_fraction_reuse",
        lambda *_args, **_kwargs: (
            [dict(item) for item in markers],
            [],
        ),
    )
    monkeypatch.setattr(
        A,
        "build_cross_sheet_summary",
        lambda *_args, **_kwargs: (None, []),
    )

    class NoopRecurringIndex:
        initial_budget = 0

        def add_sheet(self, *_args, **_kwargs):
            return {"windows_skipped": 0}

    budget = A.CrossSheetWorkBudget(
        pair_limit=10,
        value_limit=10,
        tail_match_limit=10,
        finding_limit=2,
    )
    state = A.ScanBudgetState(
        coverage=A.ScanCoverage(files_discovered=1),
        recurring_index=NoopRecurringIndex(),
        profile="review",
        evidence=False,
        cross_sheet_work_budget=budget,
    )

    result = A._process_loaded_sheet(
        A.Sheet.from_rows([[1.125]]),
        file_name="source.csv",
        sheet_name="source",
        sheet_start=None,
        state=state,
    )

    assert result.within_sheet_findings == markers[:2]
    assert budget.limitation_metadata()["findings_retained"] == 2
    assert budget.limitation_metadata()["findings_skipped"] == 1


def _patch_scan_finding_sources(monkeypatch, block_findings, cross_findings):
    def emit_block_findings(*_args, _finding_sink=None, **_kwargs):
        for item in block_findings:
            _finding_sink.offer(
                "relations",
                item["severity"],
                lambda item=item: dict(item),
            )
        return []

    monkeypatch.setattr(
        A,
        "detect_relations",
        emit_block_findings,
    )
    for name in (
        "detect_arithmetic_progression",
        "detect_equal_pairs",
        "detect_within_column_patterns",
        "detect_dispersed_repeats",
        "detect_identical_after_rounding",
        "detect_grim_grimmer",
    ):
        monkeypatch.setattr(A, name, lambda *_args, **_kwargs: [])
    monkeypatch.setattr(
        A,
        "detect_row_pair_digit_coupling",
        lambda *_args, **_kwargs: ([], {"findings_omitted": 0}),
    )
    monkeypatch.setattr(
        A,
        "detect_within_sheet_fraction_reuse",
        lambda *_args, **kwargs: (
            ([], []) if kwargs.get("with_coverage") else []
        ),
    )
    monkeypatch.setattr(
        A,
        "detect_collisions",
        lambda *_args, **_kwargs: [dict(item) for item in cross_findings],
    )
    monkeypatch.setattr(
        A,
        "detect_cross_sheet_column_duplicates",
        lambda *_args, **_kwargs: [],
    )
    monkeypatch.setattr(
        A,
        "apply_profile_to_findings",
        lambda *_args, **_kwargs: None,
    )


def _write_budget_csv(path):
    path.write_text(
        "a,b\n"
        "1.125,7.375\n"
        "2.625,4.875\n"
        "5.375,3.125\n",
        encoding="utf-8",
    )


def _retained_findings(scan):
    block = [
        finding
        for report_block in scan["relations_blocks"]
        for group in BLOCK_FINDING_GROUPS
        for finding in report_block[group]
    ]
    return block + scan["cross_sheet_findings"]


def test_directory_budget_selects_by_severity_across_finding_families(
    tmp_path, monkeypatch
):
    data = tmp_path / "data"
    data.mkdir()
    _write_budget_csv(data / "values.csv")
    _patch_scan_finding_sources(
        monkeypatch,
        block_findings=[
            {
                "kind": "block_low",
                "severity": "low",
                "rule": "low block",
            },
            {
                "kind": "block_medium",
                "severity": "medium",
                "rule": "medium block",
            },
        ],
        cross_findings=[{
            "kind": "cross_high",
            "severity": "high",
            "rule": "high cross-sheet",
        }],
    )
    monkeypatch.setattr(A, "_MAX_FINDINGS_PER_BLOCK", 0)
    monkeypatch.setattr(A, "_MAX_TOTAL_FINDINGS", 2)

    scan = scan_dir(str(data), str(tmp_path / "out"), write_html=False)

    retained = _retained_findings(scan)
    assert [finding["kind"] for finding in retained] == [
        "block_medium",
        "cross_high",
    ]
    assert scan["findings_omitted"] == 1
    assert [
        item for item in scan["coverage"]["limitations"]
        if item["reason"] == "global_finding_limit"
    ] == [{
        "scope": "scan",
        "reason": "global_finding_limit",
        "limit": 2,
        "omitted_findings": 1,
    }]


def test_directory_budget_counts_only_blocks_with_retained_findings(
    tmp_path, monkeypatch
):
    data = tmp_path / "data"
    data.mkdir()
    _write_two_sheet_blocks(data / "multi.xlsx")
    _patch_scan_finding_sources(
        monkeypatch,
        block_findings=[{
            "kind": "block_low",
            "severity": "low",
            "rule": "low block",
        }],
        cross_findings=[{
            "kind": "cross_high",
            "severity": "high",
            "rule": "high cross-sheet",
        }],
    )
    monkeypatch.setattr(A, "_MAX_FINDINGS_PER_BLOCK", 0)
    monkeypatch.setattr(A, "_MAX_TOTAL_FINDINGS", 2)

    scan = scan_dir(str(data), str(tmp_path / "out"), write_html=False)

    blocks = scan["relations_blocks"]
    empty_blocks = [
        block for block in blocks if _block_finding_count(block) == 0
    ]
    assert len(blocks) == 4
    assert len(empty_blocks) == 3
    assert all(block["findings_omitted"] == 1 for block in empty_blocks)
    assert scan["n_blocks_with_findings"] == 1
    assert [finding["kind"] for finding in scan["cross_sheet_findings"]] == [
        "cross_high",
    ]
    assert scan["findings_omitted"] == 3
    assert [
        item for item in scan["coverage"]["limitations"]
        if item["reason"] == "global_finding_limit"
    ] == [{
        "scope": "scan",
        "reason": "global_finding_limit",
        "limit": 2,
        "omitted_findings": 3,
    }]


def test_directory_budget_materializes_evidence_only_for_retained_findings(
    tmp_path, monkeypatch
):
    data = tmp_path / "data"
    data.mkdir()
    _write_budget_csv(data / "values.csv")
    _patch_scan_finding_sources(
        monkeypatch,
        block_findings=[
            {
                "kind": "block_low",
                "severity": "low",
                "rule": "low block",
            },
            {
                "kind": "block_medium",
                "severity": "medium",
                "rule": "medium block",
            },
        ],
        cross_findings=[{
            "kind": "cross_high",
            "severity": "high",
            "rule": "high cross-sheet",
        }],
    )
    monkeypatch.setattr(A, "_MAX_FINDINGS_PER_BLOCK", 0)
    monkeypatch.setattr(A, "_MAX_TOTAL_FINDINGS", 2)
    materialized = []
    original = A._block_evidence

    def record_materialization(*args, **kwargs):
        materialized.append((args, kwargs))
        return original(*args, **kwargs)

    monkeypatch.setattr(A, "_block_evidence", record_materialization)

    scan = scan_dir(str(data), str(tmp_path / "out"), write_html=False)

    assert [
        finding["kind"] for finding in _retained_findings(scan)
    ] == ["block_medium", "cross_high"]
    assert len(materialized) == 1
    retained_block_finding = (
        scan["relations_blocks"][0]["relations"][0]
    )
    assert retained_block_finding["kind"] == "block_medium"
    assert retained_block_finding["evidence"]["rows"]


def test_directory_budget_caps_cross_sheet_only_output_deterministically(
    tmp_path, monkeypatch
):
    data = tmp_path / "data"
    data.mkdir()
    _write_budget_csv(data / "values.csv")
    cross = [
        {
            "kind": "cross_first",
            "severity": "high",
            "rule": "first high",
        },
        {
            "kind": "cross_second",
            "severity": "high",
            "rule": "second high",
        },
        {
            "kind": "cross_low",
            "severity": "low",
            "rule": "low",
        },
    ]
    _patch_scan_finding_sources(
        monkeypatch,
        block_findings=[],
        cross_findings=cross,
    )
    monkeypatch.setattr(A, "_MAX_TOTAL_FINDINGS", 1)

    first = scan_dir(
        str(data), str(tmp_path / "first"), write_html=False
    )
    second = scan_dir(
        str(data), str(tmp_path / "second"), write_html=False
    )

    assert first["cross_sheet_findings"] == second["cross_sheet_findings"]
    assert [
        finding["kind"] for finding in first["cross_sheet_findings"]
    ] == ["cross_first"]
    assert first["findings_omitted"] == 2


def test_global_omissions_do_not_double_count_detector_specific_caps(
    tmp_path, monkeypatch
):
    data = tmp_path / "data"
    data.mkdir()
    _write_budget_csv(data / "values.csv")
    _patch_scan_finding_sources(
        monkeypatch,
        block_findings=[
            {
                "kind": "block_first",
                "severity": "medium",
                "rule": "first block",
            },
            {
                "kind": "block_second",
                "severity": "low",
                "rule": "second block",
            },
        ],
        cross_findings=[{
            "kind": "cross_high",
            "severity": "high",
            "rule": "high cross-sheet",
        }],
    )
    monkeypatch.setattr(A, "_MAX_FINDINGS_PER_BLOCK", 1)
    monkeypatch.setattr(A, "_MAX_TOTAL_FINDINGS", 1)

    scan = scan_dir(str(data), str(tmp_path / "out"), write_html=False)

    assert [
        finding["kind"] for finding in _retained_findings(scan)
    ] == ["cross_high"]
    assert scan["findings_omitted"] == 2
    omissions = [
        item["omitted_findings"]
        for item in scan["coverage"]["limitations"]
        if item["reason"] in {"finding_limit", "global_finding_limit"}
    ]
    assert omissions == [1, 1]


def test_row_pair_omissions_compose_with_block_and_global_caps_once(
    tmp_path, monkeypatch
):
    data = tmp_path / "data"
    data.mkdir()
    _write_budget_csv(data / "values.csv")
    _patch_scan_finding_sources(
        monkeypatch,
        block_findings=[],
        cross_findings=[],
    )
    row_pair_findings = [
        {
            "kind": f"row_pair_{severity}",
            "severity": severity,
            "rule": severity,
        }
        for severity in ("high", "medium", "low")
    ]

    def emit_row_pair_findings(
        *_args, _finding_sink=None, **_kwargs
    ):
        for item in row_pair_findings:
            _finding_sink.offer(
                "row_pairs",
                item["severity"],
                lambda item=item: dict(item),
            )
        return [], {"findings_omitted": 2}

    monkeypatch.setattr(
        A,
        "detect_row_pair_digit_coupling",
        emit_row_pair_findings,
    )
    monkeypatch.setattr(A, "_MAX_FINDINGS_PER_BLOCK", 2)
    monkeypatch.setattr(A, "_MAX_TOTAL_FINDINGS", 1)

    scan = scan_dir(
        str(data), str(tmp_path / "out"), write_html=False
    )

    assert [
        finding["kind"] for finding in _retained_findings(scan)
    ] == ["row_pair_high"]
    assert len(scan["relations_blocks"]) == 1
    assert scan["relations_blocks"][0]["findings_omitted"] == 4
    assert scan["findings_omitted"] == 4
    assert [
        (
            item["reason"],
            item["omitted_findings"],
        )
        for item in scan["coverage"]["limitations"]
        if item["reason"] in {
            "row_pair_finding_limit",
            "finding_limit",
            "global_finding_limit",
        }
    ] == [
        ("row_pair_finding_limit", 2),
        ("finding_limit", 1),
        ("global_finding_limit", 1),
    ]


def test_bounded_collector_matches_post_materialization_oracle():
    emitted = [
        ("relations", {"severity": "low", "i": 0}),
        ("relations", {"severity": "high", "i": 1}),
        ("grim", {"severity": "medium", "i": 2}),
        ("relations", {"severity": "high", "i": 3}),
        ("grim", {"severity": "low", "i": 4}),
        ("grim", {"severity": "medium", "i": 5}),
    ]
    oracle = {name: [] for name in BLOCK_FINDING_GROUPS}
    collector = BoundedFindingCollector(
        BLOCK_FINDING_GROUPS,
        cap=3,
        severity_rank=A._SEVERITY_RANK,
    )
    for group, finding in emitted:
        oracle[group].append(dict(finding))
        collector.offer(
            group,
            finding["severity"],
            lambda finding=finding: dict(finding),
        )

    omitted = _cap_block_findings(oracle, 3)
    assert collector.materialize() == oracle
    assert collector.omitted == omitted


def test_scan_path_never_retains_more_than_block_cap_during_detection(
    monkeypatch,
):
    offered = []

    def flood_relations(
        *_args, _finding_sink=None, **_kwargs
    ):
        for index in range(10_000):
            offered.append(index)
            _finding_sink.offer(
                "relations",
                "low",
                lambda index=index: {
                    "kind": "marker",
                    "severity": "low",
                    "rule": f"marker {index}",
                    "index": index,
                },
            )
            assert _finding_sink.retained <= 7
        return []

    monkeypatch.setattr(A, "_MAX_FINDINGS_PER_BLOCK", 7)
    monkeypatch.setattr(A, "detect_relations", flood_relations)
    for name in [
        "detect_arithmetic_progression",
        "detect_equal_pairs",
        "detect_within_column_patterns",
        "detect_dispersed_repeats",
        "detect_identical_after_rounding",
        "detect_grim_grimmer",
    ]:
        monkeypatch.setattr(
            A,
            name,
            lambda *_args, **_kwargs: [],
        )
    monkeypatch.setattr(
        A,
        "detect_row_pair_digit_coupling",
        lambda *_args, **_kwargs: ([], {"findings_omitted": 0}),
    )

    state = A.ScanBudgetState(
        coverage=A.ScanCoverage(files_discovered=1),
        recurring_index=A.RecurringRowIndex(budget=0),
        profile="review",
        evidence=False,
    )
    blocks = [(1, 7, 0, 2)]
    sheet = A.Sheet.from_rows(
        [["a", "b"]] + [[row + 0.125, row + 1.375] for row in range(6)]
    )

    result = A._analyze_numeric_blocks(
        sheet,
        file_name="dense.csv",
        sheet_name="dense",
        blocks=blocks,
        state=state,
    )

    assert offered == list(range(10_000))
    assert len(result[0]["relations"]) == 7
    assert result[0]["findings_omitted"] == 9_993


def test_ranked_buffer_keeps_late_best_and_stable_ties_lazily():
    calls = []
    buffer = A._BoundedRankedFindingBuffer(cap=2)

    def builder(name):
        def build():
            calls.append(name)
            return {"id": name, "severity": "high"}
        return build

    buffer.offer((1, -0.80), "medium", builder("early-medium"))
    buffer.offer((0, -0.90), "high", builder("early-high"))
    buffer.offer((0, -0.90), "high", builder("late-tie"))
    buffer.offer((0, -0.95), "high", builder("late-best"))
    findings, emit = A._finding_emitter("row_pairs", None)

    omitted = buffer.drain(emit)

    assert findings == [
        {"id": "late-best", "severity": "high"},
        {"id": "early-high", "severity": "high"},
    ]
    assert omitted == 2
    assert calls == ["late-best", "early-high"]


def test_row_pair_sink_preserves_ranked_local_cap(monkeypatch):
    header = [f"c{column}" for column in range(12)]
    base = [
        100 + column + (column + 1) / 100
        for column in range(12)
    ]
    rows = [
        header,
        base,
        [value + 10 for value in base],
        [value + 20 for value in base],
    ]
    sheet = A.Sheet.from_rows(rows)
    monkeypatch.setattr(A, "_ROW_PAIR_MAX_FINDINGS_PER_BLOCK", 1)
    baseline, baseline_meta = A.detect_row_pair_digit_coupling(
        sheet, 1, 4, 0, 12, header, with_coverage=True
    )
    collector = BoundedFindingCollector(
        BLOCK_FINDING_GROUPS,
        cap=None,
        severity_rank=A._SEVERITY_RANK,
    )

    local, sink_meta = A.detect_row_pair_digit_coupling(
        sheet,
        1,
        4,
        0,
        12,
        header,
        with_coverage=True,
        _finding_sink=collector,
    )

    assert local == []
    assert collector.materialize()["row_pairs"] == baseline
    assert sink_meta == baseline_meta == {"findings_omitted": 2}
