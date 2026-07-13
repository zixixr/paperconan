"""Unit tests for the cross-sheet collision detector (detect_collisions).

These build decimal grids directly (the same shape _grid_from_rows produces)
so we can exercise severity/context logic without round-tripping through xlsx.
"""
from __future__ import annotations

from itertools import combinations

import pytest

from paperconan import _audit as audit
from paperconan._audit import (
    CrossSheetWorkBudget,
    Sheet,
    _grid_from_rows,
    _detect_decimal_tail_reuse_for_pair,
    build_cross_sheet_summary,
    detect_collisions,
)


def _grid_from_sheet(sheet):
    grid = {}
    for r in range(sheet.nrows):
        for c in range(sheet.ncols):
            v = sheet.cell(r, c)
            if isinstance(v, (int, float)) and not isinstance(v, bool):
                grid[(r, c)] = round(float(v), 9)
    return grid


def _identical_grids(n_rows=10, n_cols=3, base=1.1001):
    """A {(r,c): value} grid with distinct decimal values, returned twice."""
    g = {}
    v = base
    for r in range(n_rows):
        for c in range(n_cols):
            g[(r, c)] = round(v, 4)
            v += 0.7137
    return dict(g), dict(g)


def _find(findings, kind):
    return next((f for f in findings if f["kind"] == kind), None)


def test_cross_sheet_finding_carries_matched_control_labels():
    rows_a = [
        ["condition", "day", "control", "treated"],
        ["rep1", 0.0, 1.23, 9.11],
        ["rep2", 1.0, 1.45, 9.31],
        ["rep3", 2.0, 1.67, 9.51],
        ["rep4", 3.0, 1.89, 9.71],
        ["rep5", 4.0, 2.01, 9.91],
        ["rep6", 5.0, 2.23, 10.11],
    ]
    rows_b = [
        ["condition", "day", "vehicle control", "drug B"],
        ["rep1", 0.0, 1.23, 4.11],
        ["rep2", 1.0, 1.45, 4.31],
        ["rep3", 2.0, 1.67, 4.51],
        ["rep4", 3.0, 1.89, 4.71],
        ["rep5", 4.0, 2.01, 4.91],
        ["rep6", 5.0, 2.23, 5.11],
    ]
    sheet_a = Sheet.from_rows(rows_a)
    sheet_b = Sheet.from_rows(rows_b)

    findings = detect_collisions(
        {("a.xlsx", "Fig. 1 control"): _grid_from_sheet(sheet_a),
         ("b.xlsx", "Fig. 2 control"): _grid_from_sheet(sheet_b)},
        sheets={("a.xlsx", "Fig. 1 control"): sheet_a,
                ("b.xlsx", "Fig. 2 control"): sheet_b},
    )

    cf = findings[0]
    assert "control" in cf["label_context_a"]["text"].lower()
    assert "vehicle control" in cf["label_context_b"]["text"].lower()
    assert cf["shared_context"]["shared_control_or_baseline"] is True


def test_cross_sheet_pair_work_budget_is_exact_and_deterministic():
    grids = {
        (f"{number}.xlsx", f"Figure {number}"): _identical_grids()[0]
        for number in range(4)
    }

    def run():
        budget = CrossSheetWorkBudget(
            pair_limit=1,
            value_limit=100_000,
            tail_match_limit=100_000,
            finding_limit=100,
        )
        findings = detect_collisions(grids, budget=budget)
        return findings, budget.limitation_metadata()

    first = run()
    second = run()

    assert first == second
    findings, metadata = first
    assert findings
    assert metadata["pairs_examined"] == 1
    assert metadata["pairs_skipped"] == 11
    assert metadata["pair_limit"] == 1
    assert metadata["limits_reached"] == ["pair"]


def _collision_and_tail_grids():
    left = {}
    right = {}
    for row in range(20):
        if row < 10:
            value = round(1.1234 + row * 0.7317, 4)
            left[(row, 0)] = value
            right[(row, 0)] = value
        else:
            tail = f"{row:04d}731"
            left[(row, 0)] = float(f"0.1{tail}")
            right[(row, 0)] = float(f"0.2{tail}")
    return left, right


def test_pair_budget_admits_detector_families_independently():
    ga, gb = _collision_and_tail_grids()
    grids = {
        ("a.xlsx", "Figure 1"): ga,
        ("b.xlsx", "Figure 2"): gb,
    }

    one_family = CrossSheetWorkBudget(
        pair_limit=1,
        value_limit=100_000,
        tail_match_limit=100_000,
        finding_limit=100,
    )
    one_family_findings = detect_collisions(
        grids, budget=one_family
    )
    two_families = CrossSheetWorkBudget(
        pair_limit=2,
        value_limit=100_000,
        tail_match_limit=100_000,
        finding_limit=100,
    )
    two_family_findings = detect_collisions(
        grids, budget=two_families
    )

    assert _find(
        one_family_findings, "cross_sheet_position_identical"
    )
    assert _find(
        one_family_findings, "cross_sheet_decimal_tail_reuse"
    ) is None
    assert one_family.limitation_metadata()["pairs_examined"] == 1
    assert one_family.limitation_metadata()["pairs_skipped"] == 1
    assert _find(
        two_family_findings, "cross_sheet_position_identical"
    )
    assert _find(
        two_family_findings, "cross_sheet_decimal_tail_reuse"
    )
    assert two_families.limitation_metadata()["pairs_examined"] == 2
    assert two_families.limitation_metadata()["pairs_skipped"] == 0


def _sized_grid(size, offset=0.0):
    return {
        (row, 0): round(offset + 1.1234 + row * 0.7317, 4)
        for row in range(size)
    }


class _VisitGrid(dict):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.value_visits = 0

    def values(self):
        for value in super().values():
            self.value_visits += 1
            yield value

    def items(self):
        for item in super().items():
            self.value_visits += 1
            yield item


class _CountingGrid(dict):
    def __init__(self, values):
        super().__init__(values)
        self.item_visits = 0

    def items(self):
        for item in super().items():
            self.item_visits += 1
            yield item


@pytest.mark.parametrize("distinct", [False, True])
def test_pair_stats_use_one_exact_source_pass_per_grid(distinct):
    if distinct:
        left = _VisitGrid(_sized_grid(12))
        right = _VisitGrid(_sized_grid(9))
    else:
        left = _VisitGrid({
            (row, 0): float(row % 2) + 0.125
            for row in range(12)
        })
        right = _VisitGrid({
            (row, 0): float(row % 2) + 0.125
            for row in range(9)
        })

    _stats, coverage = audit._cross_sheet_pair_stats(
        left, right, with_coverage=True
    )

    assert coverage["value_visits"] == len(left) + len(right)
    assert left.value_visits == len(left)
    assert right.value_visits == len(right)


def test_cross_sheet_value_work_counts_known_passes_exactly():
    grids = {
        ("a.xlsx", "Figure 1"): _sized_grid(9),
        ("b.xlsx", "Figure 2"): _sized_grid(8),
    }
    budget = CrossSheetWorkBudget(
        pair_limit=2,
        value_limit=100_000,
        tail_match_limit=100_000,
        finding_limit=100,
    )
    detect_collisions(grids, budget=budget)

    metadata = budget.limitation_metadata()
    assert metadata["pairs_examined"] == 2
    assert metadata["pairs_skipped"] == 0
    assert metadata["values_examined"] == (
        4 * (9 + 8)
        + 4 * 2
        + (9 + 8)
        + (9 + 8)
    )
    assert metadata["values_skipped"] == 0


def test_pair_value_budget_stops_before_any_uncharged_pass():
    left = _VisitGrid(_sized_grid(10))
    right = _VisitGrid(_sized_grid(8))
    grids = {
        ("a.xlsx", "Figure 1"): left,
        ("b.xlsx", "Figure 2"): right,
    }
    axis_work = 4 * (len(left) + len(right)) + 4 * 2
    family_work = len(left) + len(right)
    rejected = CrossSheetWorkBudget(
        pair_limit=2,
        value_limit=axis_work + family_work - 1,
        tail_match_limit=100_000,
        finding_limit=100,
    )

    detect_collisions(grids, budget=rejected)

    rejected_meta = rejected.limitation_metadata()
    assert left.value_visits == len(left)
    assert right.value_visits == len(right)
    assert rejected_meta["pairs_examined"] == 0
    assert rejected_meta["pairs_skipped"] == 2
    assert rejected_meta["values_examined"] == axis_work
    assert rejected_meta["values_skipped"] == 2 * family_work


def test_pair_stop_reports_exact_remaining_family_and_value_work():
    grids = {
        ("a.xlsx", "Figure 1"): _sized_grid(6, 0),
        ("b.xlsx", "Figure 2"): _sized_grid(8, 100),
        ("c.xlsx", "Figure 3"): _sized_grid(9, 200),
    }
    budget = CrossSheetWorkBudget(
        pair_limit=1,
        value_limit=100_000,
        tail_match_limit=100_000,
        finding_limit=100,
    )
    detect_collisions(grids, budget=budget)

    metadata = budget.limitation_metadata()
    assert metadata["pairs_examined"] == 1
    assert metadata["pairs_skipped"] == 3
    assert metadata["values_examined"] == 4 * 23 + 4 * 3 + 14
    assert metadata["values_skipped"] == 49
    assert metadata["limits_reached"] == ["pair"]


def test_impossible_families_do_not_displace_later_viable_pair():
    viable = _sized_grid(8)
    grids = {
        ("a.xlsx", "Figure 1"): _sized_grid(5, 100),
        ("b.xlsx", "Figure 2"): _sized_grid(5, 200),
        ("c.xlsx", "Figure 3"): dict(viable),
        ("d.xlsx", "Figure 4"): dict(viable),
    }
    budget = CrossSheetWorkBudget(
        pair_limit=1,
        value_limit=100_000,
        tail_match_limit=100_000,
        finding_limit=100,
    )
    findings = detect_collisions(grids, budget=budget)

    assert _find(findings, "cross_sheet_position_identical")
    metadata = budget.limitation_metadata()
    assert metadata["pairs_examined"] == 1
    assert metadata["pairs_skipped"] == 1
    assert metadata["values_examined"] == (
        3 * (5 + 5 + 2 * len(viable))
        + 2 * len(viable)
        + 4 * 4
        + 16
    )
    assert metadata["values_skipped"] == 16
    assert metadata["limits_reached"] == ["pair"]


@pytest.mark.parametrize("pair_limit", [0, 1])
def test_pair_setup_is_linear_and_remaining_work_is_exact(
    monkeypatch, pair_limit
):
    summary_count = 200
    grid_size = 8
    grids = {
        (f"{number}.xlsx", f"Figure {number}"): _sized_grid(
            grid_size, number * 100
        )
        for number in range(summary_count)
    }
    advances = 0
    original_combinations = combinations

    def tracked_combinations(iterable, size):
        nonlocal advances
        for pair in original_combinations(iterable, size):
            advances += 1
            yield pair

    monkeypatch.setattr(audit, "combinations", tracked_combinations)
    budget = CrossSheetWorkBudget(
        pair_limit=pair_limit,
        value_limit=10_000_000,
        tail_match_limit=100_000,
        finding_limit=100,
    )

    detect_collisions(grids, budget=budget)

    family_pairs = summary_count * (summary_count - 1)
    family_value_work = family_pairs * (2 * grid_size)
    examined_work = pair_limit * (2 * grid_size)
    metadata = budget.limitation_metadata()
    assert advances == 1
    assert metadata["pairs_examined"] == pair_limit
    assert metadata["pairs_skipped"] == family_pairs - pair_limit
    assert metadata["values_examined"] == (
        4 * summary_count * grid_size
        + 4 * summary_count
        + examined_work
    )
    assert metadata["values_skipped"] == (
        family_value_work - examined_work
    )


def test_collision_context_uses_a_bounded_shared_cell_sample(monkeypatch):
    ga, gb = _identical_grids(n_rows=50, n_cols=2)
    sample_sizes = []
    original = audit._label_context_for_matches

    def tracked_context(sheet, shared, max_labels=40):
        sample_sizes.append(len(shared))
        return original(sheet, shared, max_labels=max_labels)

    monkeypatch.setattr(
        audit, "_label_context_for_matches", tracked_context
    )

    detect_collisions({
        ("a.xlsx", "Figure 1"): ga,
        ("b.xlsx", "Figure 2"): gb,
    })

    assert sample_sizes
    assert max(sample_sizes) <= 40


def test_decimal_tail_match_state_stops_before_limit_is_exceeded():
    ga, gb = {}, {}
    for row in range(40):
        tail = f"{row:04d}731"
        ga[(row, 0)] = float(f"0.1{tail}")
        gb[(row, 0)] = float(f"0.2{tail}")
    ga = _VisitGrid(ga)
    gb = _VisitGrid(gb)
    budget = CrossSheetWorkBudget(
        pair_limit=10,
        value_limit=10_000,
        tail_match_limit=3,
        finding_limit=10,
    )

    result = _detect_decimal_tail_reuse_for_pair(
        ga, gb, min_matches=2, budget=budget
    )

    assert result is None
    metadata = budget.limitation_metadata()
    assert metadata["tail_matches_retained"] == 3
    assert metadata["tail_matches_skipped_lower_bound"] == 1
    assert metadata["pairs_examined"] == 1
    assert metadata["pairs_skipped"] == 0
    assert metadata["values_examined"] == (
        ga.value_visits + gb.value_visits
    )
    assert metadata["values_skipped"] == (
        len(ga) + len(gb) - metadata["values_examined"]
    )
    assert metadata["limits_reached"] == ["tail_match"]


def test_cross_sheet_pre_cap_finding_budget_is_hard_bounded():
    grids = {
        (f"{number}.xlsx", f"Figure {number}"): _identical_grids()[0]
        for number in range(4)
    }
    budget = CrossSheetWorkBudget(
        pair_limit=100,
        value_limit=1_000_000,
        tail_match_limit=1_000_000,
        finding_limit=2,
    )

    findings = detect_collisions(grids, budget=budget)

    assert len(findings) == 2
    metadata = budget.limitation_metadata()
    assert metadata["findings_retained"] == 2
    assert metadata["findings_skipped"] >= 4
    assert metadata["finding_limit"] == 2
    assert "finding" in metadata["limits_reached"]


def test_cross_sheet_context_marks_time_axis_from_local_labels():
    rows_a = [
        ["sample", "time", "signal"],
        ["r1", 0, 1.1],
        ["r2", 1, 1.3],
        ["r3", 2, 1.5],
        ["r4", 3, 1.7],
        ["r5", 4, 1.9],
        ["r6", 5, 2.1],
    ]
    rows_b = [
        ["sample", "time", "signal"],
        ["r1", 0, 8.1],
        ["r2", 1, 8.3],
        ["r3", 2, 8.5],
        ["r4", 3, 8.7],
        ["r5", 4, 8.9],
        ["r6", 5, 9.1],
    ]
    sheet_a = Sheet.from_rows(rows_a)
    sheet_b = Sheet.from_rows(rows_b)

    cf = detect_collisions(
        {("a.xlsx", "Fig. 1"): _grid_from_sheet(sheet_a),
         ("b.xlsx", "Fig. 2"): _grid_from_sheet(sheet_b)},
        sheets={("a.xlsx", "Fig. 1"): sheet_a,
                ("b.xlsx", "Fig. 2"): sheet_b},
    )[0]

    assert cf["shared_context"]["shared_axis_or_coordinate"] is True
    assert cf["delta"]["pattern"] != "perfect_dup"


def test_compact_label_context_matches_sheet_compatibility_path():
    rows_a = [
        ["condition", "control", "treated"],
        ["rep1", 1.2345, 9.1111],
        ["rep2", 1.4567, 9.3131],
        ["rep3", 1.6789, 9.5151],
        ["rep4", 1.8901, 9.7171],
        ["rep5", 2.0123, 9.9191],
        ["rep6", 2.2345, 10.1111],
    ]
    rows_b = [
        ["condition", "vehicle control", "drug B"],
        ["rep1", 1.2345, 4.1111],
        ["rep2", 1.4567, 4.3131],
        ["rep3", 1.6789, 4.5151],
        ["rep4", 1.8901, 4.7171],
        ["rep5", 2.0123, 4.9191],
        ["rep6", 2.2345, 5.1111],
    ]
    sheets = {
        ("a.xlsx", "Fig. 1 control"): Sheet.from_rows(rows_a),
        ("b.xlsx", "Fig. 2 control"): Sheet.from_rows(rows_b),
    }
    summaries = {
        key: build_cross_sheet_summary(*key, source)[0]
        for key, source in sheets.items()
    }
    grids = {key: _grid_from_rows(source) for key, source in sheets.items()}

    direct = detect_collisions(grids, sheets=sheets)
    compact = detect_collisions(
        {key: summary.grid for key, summary in summaries.items()},
        sheets={key: summary.labels for key, summary in summaries.items()},
    )

    assert compact == direct


# ---------- Issue 1: context-aware severity ----------

def test_same_figure_same_file_overlap_is_downgraded():
    """Two panels of the SAME figure in the SAME file sharing identical data is
    the expected combined-vs-individual re-plot — must be downgraded, not high."""
    ga, gb = _identical_grids()
    grids = {
        ("MOESM16.xlsx", "exFig.6i"): ga,
        ("MOESM16.xlsx", "exFig.6k-n"): gb,
    }
    findings = detect_collisions(grids)
    assert findings, "expected a collision finding"
    cf = findings[0]
    assert cf["same_figure"] is True
    assert cf["figure_a"] == cf["figure_b"]
    assert cf["severity"] == "low", f"same-figure re-plot should be low, got {cf['severity']}"
    assert cf.get("context"), "same-figure finding should carry a benign context note"


def test_cross_figure_cross_file_overlap_keeps_severity():
    """Main Fig 5o vs Extended Fig 6b-e — different figures, different files.
    This is the one worth attention: severity must NOT be downgraded."""
    ga, gb = _identical_grids()
    grids = {
        ("MOESM8.xlsx", "Figure 5o"): ga,
        ("MOESM16.xlsx", "exFig.6b-e"): gb,
    }
    findings = detect_collisions(grids)
    cf = findings[0]
    assert cf["same_figure"] is False
    assert cf["figure_a"] != cf["figure_b"]
    assert cf["severity"] == "high", \
        f"cross-figure position-identical should stay high, got {cf['severity']}"


def test_unparseable_sheet_names_are_not_same_figure():
    """If we can't parse a figure id from the sheet name, never claim same_figure."""
    ga, gb = _identical_grids()
    grids = {
        ("a.xlsx", "Sheet1"): ga,
        ("a.xlsx", "Sheet2"): gb,
    }
    cf = detect_collisions(grids)[0]
    assert cf["same_figure"] is False
    assert cf["severity"] == "high"


# ---------- Issue 2: near-duplicate delta characterization ----------

def test_delta_perfect_dup():
    """Two identical tables — a clean re-plot. Pattern must be perfect_dup."""
    ga, gb = _identical_grids()
    cf = detect_collisions({("a.xlsx", "Sheet1"): ga, ("a.xlsx", "Sheet2"): gb})[0]
    delta = cf["delta"]
    assert delta["pattern"] == "perfect_dup"
    assert delta["only_in_a"] == 0 and delta["only_in_b"] == 0
    assert delta["modified_cells"] == 0


def test_delta_superset_extra_column():
    """B = A plus one extra replicate column (new positions, new values), nothing
    altered. This is the benign 'main shows n=5, extended shows n=6' shape —
    pattern superset, modified_cells == 0, extras only on one side."""
    ga, _ = _identical_grids(n_rows=10, n_cols=3)
    gb = dict(ga)
    extra_v = 900.1234
    for r in range(10):  # an extra 4th column present only in B
        gb[(r, 3)] = round(extra_v, 4)
        extra_v += 0.55
    cf = detect_collisions({("a.xlsx", "Sheet1"): ga, ("a.xlsx", "Sheet2"): gb})[0]
    delta = cf["delta"]
    assert delta["pattern"] == "superset"
    assert delta["modified_cells"] == 0
    assert delta["only_in_a"] == 0
    assert delta["only_in_b"] >= 10


def test_delta_value_tweaked():
    """B is a copy of A with a few cells changed in place (same position, new value).
    This is the copy-then-tweak fingerprint — pattern value_tweaked, the most
    forensically interesting, distinct from a clean re-plot."""
    ga, gb = _identical_grids(n_rows=10, n_cols=3)
    gb[(0, 0)] = ga[(0, 0)] + 0.0009
    gb[(5, 2)] = ga[(5, 2)] + 0.0011
    cf = detect_collisions({("a.xlsx", "Sheet1"): ga, ("a.xlsx", "Sheet2"): gb})[0]
    delta = cf["delta"]
    assert delta["modified_cells"] == 2
    assert delta["pattern"] == "value_tweaked"


def test_detects_cross_sheet_decimal_tail_reuse_with_shifted_layout():
    """B copies A's measurement block, shifts it up two rows, and edits only the
    high-order decimal digit. Exact-value overlap misses this, but the long
    fractional tails remain aligned at one table offset.
    """
    ga, gb = {}, {}
    for r in range(12):
        for c in range(3):
            tail = f"{r:02d}{c:02d}731"
            ga[(r + 5, c + 2)] = float(f"0.{(r + c) % 9}{tail}")
            gb[(r + 3, c + 2)] = float(f"0.{((r + c) % 9 + 3) % 10}{tail}")

    findings = detect_collisions({
        ("M.xlsx", "Figure 5d"): ga,
        ("M.xlsx", "Supplementary Figure 6g"): gb,
    })

    tail = _find(findings, "cross_sheet_decimal_tail_reuse")
    assert tail is not None
    assert tail["offset_rows"] == -2
    assert tail["offset_cols"] == 0
    assert tail["tail_match_count"] == 36
    assert tail["severity"] == "high"
    assert tail["examples"][0]["value_a"] != tail["examples"][0]["value_b"]


def test_decimal_tail_reuse_requires_long_tail_cluster_not_short_decimals():
    ga, gb = {}, {}
    for r in range(20):
        ga[(r, 0)] = round(1.1 + r * 0.1, 1)
        gb[(r, 0)] = round(5.1 + r * 0.1, 1)

    findings = detect_collisions({
        ("M.xlsx", "Figure 1"): ga,
        ("M.xlsx", "Figure 2"): gb,
    })

    assert _find(findings, "cross_sheet_decimal_tail_reuse") is None


def test_decimal_tail_reuse_fixed_denominator_is_downgraded_with_reason():
    ga, gb = {}, {}
    shifts = [1, 3, 2, 4, 1, 5, 2, 6, 3, 7, 4, 8]
    for r, shift in enumerate(shifts):
        va = (r + 1) / 7
        ga[(r, 0)] = va
        gb[(r, 0)] = va + shift

    findings = detect_collisions({
        ("M.xlsx", "Figure 2"): ga,
        ("M.xlsx", "Figure 3"): gb,
    }, profile="forensic")

    tail = _find(findings, "cross_sheet_decimal_tail_reuse")
    assert tail is not None
    assert tail["severity"] == "low"
    assert tail["tail_benign_reason"] == "fixed_denominator:1/7"


def test_delta_shifted_layout_is_perfect_dup_not_tweaked():
    """Same numbers stored at a different column offset (a main figure and an
    extended figure laying the cohort out differently). The value multiset is
    identical, so this is a perfect_dup of the data — NOT value_tweaked, even
    though raw (row,col) positions disagree."""
    ga, _ = _identical_grids(n_rows=10, n_cols=3)
    gb = {(r, c + 1): v for (r, c), v in ga.items()}  # shift every cell one column right
    cf = detect_collisions({("M8.xlsx", "Figure 5o"): ga,
                            ("M8.xlsx", "Figure 5o2"): gb})[0]
    delta = cf["delta"]
    assert delta["only_in_a"] == 0 and delta["only_in_b"] == 0
    assert delta["pattern"] == "perfect_dup", \
        f"identical value multiset must read as perfect_dup, got {delta['pattern']}"


# ---------- Issue 3: shared-axis overlap downgrade ----------
# A cross-figure overlap whose shared (row,col) cells concentrate on a column that
# is an axis (serial-dilution dose ladder, swept time/field axis, or a column reused
# across many sheets) is a shared-x-axis artifact, not cross-experiment reuse. It must
# be downgraded — but only when the rest of the table diverges (pattern != perfect_dup);
# a full-table duplicate stays high.

def test_shared_dose_axis_overlap_is_downgraded():
    """Fig 3e and Fig 5b are two dose-response curves: they share the identical
    serial-dilution (1:3) concentration column at the same positions, but the
    measured values differ. The overlap is the dose axis — must be downgraded."""
    dose = [16.6667, 5.55556, 1.85185, 0.617284, 0.205761, 0.0685871, 0.0228624, 0.00762080]
    ga, gb = {}, {}
    for r, d in enumerate(dose):
        ga[(r, 0)] = round(d, 6); gb[(r, 0)] = round(d, 6)          # shared dose axis
        ga[(r, 1)] = round(10.0 + r * 0.3137, 4); ga[(r, 2)] = round(50.0 - r * 0.71, 4)
        gb[(r, 1)] = round(90.0 - r * 0.41, 4);   gb[(r, 2)] = round(3.0 + r * 0.55, 4)
    cf = detect_collisions({("M.xlsx", "Fig. 3e"): ga, ("M.xlsx", "Fig. 5b"): gb})[0]
    assert cf["kind"] == "cross_sheet_position_identical"
    assert cf["same_figure"] is False
    assert cf["delta"]["pattern"] != "perfect_dup"
    assert cf.get("axis_overlap") is True
    assert cf["severity"] == "low", f"shared dose-axis overlap should be low, got {cf['severity']}"
    assert cf.get("likely_benign")


def test_recurring_axis_column_across_sheets_is_downgraded():
    """A column whose value-set recurs across >=3 sheets is a shared axis even when
    it is not a clean progression. A cross-figure pair sharing only that column
    must be downgraded."""
    axis = [0.1234, 0.8765, 0.4567, 0.9876, 0.3210, 0.6540, 0.2222]  # not a progression
    def mk(base):
        g = {}
        for r, a in enumerate(axis):
            g[(r, 0)] = round(a, 6)
            g[(r, 1)] = round(base + r * 0.137, 4)   # distinct measurement per sheet
        return g
    grids = {("M.xlsx", "Figure 1O"): mk(10.0),
             ("M.xlsx", "sFigure 2D"): mk(40.0),
             ("M.xlsx", "Figure 5D"): mk(70.0)}
    findings = detect_collisions(grids)
    pair = next(f for f in findings
                if {f["sheet_a"], f["sheet_b"]} == {"Figure 1O", "sFigure 2D"})
    assert pair.get("axis_overlap") is True
    assert pair["severity"] == "low"


def test_full_table_dup_not_downgraded_by_axis_rule():
    """A cross-figure overlap where EVERY column matches (perfect_dup) is a full
    duplicate / re-plot — it must stay high regardless of the axis rule."""
    ga, gb = _identical_grids()
    cf = detect_collisions({("M8.xlsx", "Figure 5o"): ga,
                            ("M16.xlsx", "exFig.6b-e"): gb})[0]
    assert cf["delta"]["pattern"] == "perfect_dup"
    assert cf["severity"] == "high"
    assert cf.get("axis_overlap") is not True


def test_copied_measurement_column_keeps_severity():
    """Boundary guard: a pair that shares an axis AND a copied (realistic, non-progression)
    MEASUREMENT column — with a third column divergent — must stay HIGH. The duplicated
    measurement is the forensic signal; only the axis being shared must not buy a downgrade."""
    axis = [16.6667, 5.55556, 1.85185, 0.617284, 0.205761, 0.0685871, 0.0228624, 0.00762080]
    meas = [12.7431, 3.1188, 88.4502, 7.6613, 41.2099, 0.9931, 23.8847, 55.0024]  # not a progression
    ga, gb = {}, {}
    for r in range(len(axis)):
        ga[(r, 0)] = round(axis[r], 6); gb[(r, 0)] = round(axis[r], 6)   # shared axis
        ga[(r, 1)] = meas[r];           gb[(r, 1)] = meas[r]             # COPIED measurement
        ga[(r, 2)] = round(10.0 + r * 0.3137, 4)
        gb[(r, 2)] = round(90.0 - r * 0.41, 4)                          # divergent column
    cf = detect_collisions({("M.xlsx", "Fig. 3e"): ga, ("M.xlsx", "Fig. 5b"): gb})[0]
    assert cf["same_figure"] is False
    assert cf.get("axis_overlap") is not True, "a copied measurement column must not be treated as axis"
    assert cf["severity"] == "high"


# ---------------------------------------------------------------------------
# B1: cross_sheet_column_duplicate — full-column duplication across panels,
# including the integer / 1-decimal columns detect_collisions' >=3dp grids miss.
# ---------------------------------------------------------------------------
from paperconan._audit import detect_cross_sheet_column_duplicates, figure_key  # noqa: E402


def _sheet_from_cols(labels, cols):
    rows = [list(labels)]
    for r in range(len(cols[0])):
        rows.append([cols[j][r] for j in range(len(cols))])
    return Sheet.from_rows(rows)


def _b1_oracle(panels):
    """Independent ground truth. panels: {(file,sheet): {label: values}}. Returns the set of
    frozenset({(file,sheet), (file,sheet)}) pairs that SHOULD be a HIGH duplicate: byte-identical
    high-cardinality non-axis column of len>=12, different figure namespaces, and not the
    all-integer-short case."""
    import numpy as np
    cols = []
    for (f, s), colmap in panels.items():
        for label, vals in colmap.items():
            cols.append((f, s, label, [float(v) for v in vals]))
    def axis_like(a):
        if len(set(round(v, 9) for v in a)) <= 1:
            return True
        d = np.diff(a)
        return bool(np.allclose(d, d[0], atol=1e-9 * max(max(abs(x) for x in a), 1e-300), rtol=1e-9) and abs(d[0]) > 0)
    high = set()
    for i in range(len(cols)):
        for j in range(i + 1, len(cols)):
            fa, sa, _la, a = cols[i]
            fb, sb, _lb, b = cols[j]
            if (fa, sa) == (fb, sb) or len(a) != len(b):
                continue
            if [round(x, 6) for x in a] != [round(x, 6) for x in b]:
                continue
            n = len(a)
            if n < 12 or axis_like(a) or len(set(round(v, 9) for v in a)) < max(6, n // 2):
                continue
            all_int = all(abs(v - round(v)) < 1e-9 for v in a)
            if all_int and (n < 25 or len(set(round(v, 9) for v in a)) < max(12, int(0.7 * n))):
                continue
            fka, fkb = figure_key(sa), figure_key(sb)
            if fka is not None and fka == fkb:
                continue  # same figure -> low, not high
            high.add(frozenset({(fa, sa), (fb, sb)}))
    return high


def _run_b1(panels):
    gs = {k: _sheet_from_cols(list(v.keys()), list(v.values())) for k, v in panels.items()}
    return detect_cross_sheet_column_duplicates(gs)


def test_b1_flags_cross_figure_column_duplicate_and_matches_oracle():
    dup = [3.0, 3.2, 2.5, 2.8, 2.9, 2.2, 5.0, 5.2, 4.5, 4.8, 4.9, 4.2, 6.1, 6.3, 5.7]  # 15, 1-dp
    other = [1.1, 2.4, 3.9, 0.7, 5.5, 4.2, 6.8, 2.1, 3.3, 7.4, 1.9, 8.2, 0.5, 4.7, 6.0]
    panels = {
        ("F3.xls", "Figure 3b"): {"NBS1-K388R": dup, "ctrl": other},
        ("F9.xls", "Extended Data Fig. 9d"): {"Si-LDHA": list(dup), "misc": list(reversed(other))},
    }
    f = _run_b1(panels)
    hi = [x for x in f if x["severity"] == "high"]
    assert len(hi) == 1, f
    assert hi[0]["size_a"] == 15 and hi[0]["same_figure"] is False
    got = {frozenset({(x["file_a"], x["sheet_a"]), (x["file_b"], x["sheet_b"])}) for x in hi}
    assert got == _b1_oracle(panels)


def test_b1_no_flag_on_shared_axis_column():
    axis = [0.5 * (i + 1) for i in range(15)]        # perfect progression → axis
    panels = {
        ("A.xls", "Figure 1a"): {"week": list(axis), "m": [1.1 * i + 0.3 for i in range(15)]},
        ("B.xls", "Figure 2a"): {"week": list(axis), "m": [9.0 - 0.2 * i for i in range(15)]},
    }
    f = _run_b1(panels)
    assert not [x for x in f if x["severity"] == "high"]
    assert _b1_oracle(panels) == set()


def test_b1_same_figure_is_low_not_high():
    dup = [3.0, 3.2, 2.5, 2.8, 2.9, 2.2, 5.0, 5.2, 4.5, 4.8, 4.9, 4.2, 6.1]
    panels = {
        ("M.xls", "Figure 4b"): {"control": list(dup)},
        ("M.xls", "Figure 4c"): {"control": list(dup)},
    }
    f = _run_b1(panels)
    assert not [x for x in f if x["severity"] == "high"]
    assert all(x["severity"] == "low" for x in f)


def test_b1_no_flag_short_or_all_integer_column():
    short = [3.0, 3.2, 2.5, 2.8, 2.9, 2.2]           # < 12
    ints = [int(v) for v in range(100, 118)]          # all-integer, n=18 < 25
    panels = {
        ("A.xls", "Figure 1a"): {"s": short, "i": list(ints)},
        ("B.xls", "Figure 2a"): {"s": list(short), "i": list(ints)},
    }
    f = _run_b1(panels)
    assert not f
    assert _b1_oracle(panels) == set()


def test_b1_serial_dilution_axis_not_flagged():
    # regression: _column_axis_like rejected only arithmetic ladders, so a geometric
    # serial-dilution axis shared across two dose-response panels was flagged HIGH (FP).
    serial = [100.0 / (2 ** i) for i in range(14)]
    other_a = [1.1 * i + 0.3 for i in range(14)]
    other_b = [9.0 - 0.2 * i for i in range(14)]
    panels = {
        ("A.xls", "Figure 1a"): {"dose": list(serial), "m": other_a},
        ("B.xls", "Figure 2a"): {"dose": list(serial), "m": other_b},
    }
    f = _run_b1(panels)
    assert not [x for x in f if x["severity"] == "high"], "a shared serial-dilution axis is benign"


def test_b1_rule_wording_is_honest_about_precision():
    dup = [3.0, 3.2, 2.5, 2.8, 2.9, 2.2, 5.0, 5.2, 4.5, 4.8, 4.9, 4.2, 6.1, 6.3, 5.7]
    panels = {
        ("F3.xls", "Figure 3b"): {"x": dup},
        ("F9.xls", "Extended Data Fig. 9d"): {"y": list(dup)},
    }
    hi = [x for x in _run_b1(panels) if x["severity"] == "high"]
    assert hi and "byte-identical" not in hi[0]["rule"]
    assert "6 decimal places" in hi[0]["rule"]


def test_b1_single_sheet_early_exit():
    from paperconan._audit import detect_cross_sheet_column_duplicates
    one = {("A.xls", "Figure 1a"): _sheet_from_cols(["a", "b"], [[1.1 * i + 0.3 for i in range(14)],
                                                                 [9.0 - 0.2 * i for i in range(14)]])}
    assert detect_cross_sheet_column_duplicates(one) == []


def test_column_duplicate_comparisons_share_pair_and_finding_budget():
    duplicate = [
        3.0, 3.2, 2.5, 2.8, 2.9, 2.2, 5.0, 5.2,
        4.5, 4.8, 4.9, 4.2, 6.1, 6.3, 5.7,
    ]
    summaries = [
        build_cross_sheet_summary(
            f"{number}.xlsx",
            f"Figure {number}",
            _sheet_from_cols(["value"], [duplicate]),
        )[0]
        for number in range(4)
    ]
    budget = CrossSheetWorkBudget(
        pair_limit=2,
        value_limit=100_000,
        tail_match_limit=100_000,
        finding_limit=1,
    )

    findings = detect_cross_sheet_column_duplicates(
        summaries, budget=budget
    )

    assert len(findings) == 1
    metadata = budget.limitation_metadata()
    assert metadata["pairs_examined"] == 2
    assert metadata["pairs_skipped"] == 4
    assert metadata["findings_retained"] == 1
    assert metadata["findings_skipped"] == 1
    assert metadata["limits_reached"] == ["pair", "finding"]


def _duplicate_column_summaries(count):
    duplicate = [
        3.0, 3.2, 2.5, 2.8, 2.9, 2.2, 5.0, 5.2,
        4.5, 4.8, 4.9, 4.2, 6.1, 6.3, 5.7,
    ]
    return [
        build_cross_sheet_summary(
            f"{number}.xlsx",
            f"Figure {number}",
            _sheet_from_cols(["value"], [duplicate]),
        )[0]
        for number in range(count)
    ]


def test_column_duplicate_bucket_preserves_legacy_first_ten_findings():
    summaries = _duplicate_column_summaries(6)
    budget = CrossSheetWorkBudget(
        pair_limit=100,
        value_limit=100_000,
        tail_match_limit=100_000,
        finding_limit=100,
    )

    findings = detect_cross_sheet_column_duplicates(
        summaries, budget=budget
    )

    expected_pairs = [
        (f"Figure {left}", f"Figure {right}")
        for left, right in combinations(range(6), 2)
    ][:10]
    assert [
        (finding["sheet_a"], finding["sheet_b"])
        for finding in findings
    ] == expected_pairs
    metadata = budget.limitation_metadata()
    assert metadata["pairs_examined"] == 15
    assert metadata["pairs_skipped"] == 0
    assert metadata["findings_retained"] == 10
    assert metadata["bucket_findings_skipped"] == 5
    assert metadata["findings_skipped"] == 5
    assert metadata["limits_reached"] == ["fingerprint_bucket"]


def test_column_duplicate_bucket_and_global_finding_omissions_are_exact():
    summaries = _duplicate_column_summaries(6)
    budget = CrossSheetWorkBudget(
        pair_limit=100,
        value_limit=100_000,
        tail_match_limit=100_000,
        finding_limit=8,
    )

    findings = detect_cross_sheet_column_duplicates(
        summaries, budget=budget
    )

    assert len(findings) == 8
    metadata = budget.limitation_metadata()
    assert metadata["pairs_examined"] == 15
    assert metadata["findings_retained"] == 8
    assert metadata["bucket_findings_skipped"] == 5
    assert metadata["findings_skipped"] == 7
    assert metadata["limits_reached"] == [
        "finding",
        "fingerprint_bucket",
    ]


def test_axis_work_matches_concrete_passes_for_feasible_grids_only():
    irrelevant = _CountingGrid({
        (row, 0): row + 0.125 for row in range(3)
    })
    recurrence_support = _CountingGrid({
        (row, 0): row + 20.625 for row in range(5)
    })
    left = _CountingGrid({
        (row, 0): row + 0.125 for row in range(8)
    })
    right = _CountingGrid({
        (row, 0): row + 10.375 for row in range(8)
    })
    budget = CrossSheetWorkBudget(
        pair_limit=100,
        value_limit=10_000,
        tail_match_limit=100,
        finding_limit=100,
    )

    axis, coverage = audit._axis_columns(
        {
            ("irrelevant.csv", "small"): irrelevant,
            ("support.csv", "support"): recurrence_support,
            ("left.csv", "Figure 1"): left,
            ("right.csv", "Figure 2"): right,
        },
        budget=budget,
        with_coverage=True,
    )

    assert irrelevant.item_visits == 0
    assert recurrence_support.item_visits == len(
        recurrence_support
    )
    assert left.item_visits == len(left)
    assert right.item_visits == len(right)
    assert {
        name: coverage[name]
        for name in (
            "participating_summaries",
            "participating_cells",
            "recurrence_support_summaries",
            "recurrence_support_cells",
            "axis_loading_visits",
            "axis_grouping_visits",
            "axis_progression_visits",
            "axis_fingerprint_visits",
            "axis_recurrence_order_visits",
            "axis_recurrence_group_visits",
            "axis_recurrence_comparison_visits",
            "axis_recurrence_mark_visits",
            "axis_output_visits",
            "axis_value_visits",
            "axis_context_available",
        )
    } == {
        "participating_summaries": 2,
        "participating_cells": 16,
        "recurrence_support_summaries": 3,
        "recurrence_support_cells": 21,
        "axis_loading_visits": 21,
        "axis_grouping_visits": 21,
        "axis_progression_visits": 16,
        "axis_fingerprint_visits": 21,
        "axis_recurrence_order_visits": 3,
        "axis_recurrence_group_visits": 3,
        "axis_recurrence_comparison_visits": 3,
        "axis_recurrence_mark_visits": 0,
        "axis_output_visits": 3,
        "axis_value_visits": 91,
        "axis_context_available": True,
    }
    assert coverage["axis_state_unit_limit"] == (
        audit._AXIS_STATE_UNITS_PER_CELL * 21
    )
    assert 0 < coverage["axis_peak_state_units"] <= (
        coverage["axis_state_unit_limit"]
    )
    assert budget.values_examined == 91
    assert set(axis) == {
        ("left.csv", "Figure 1"),
        ("right.csv", "Figure 2"),
    }


def test_axis_fingerprint_preserves_signed_zero_set_equivalence():
    def build_grids(zeros):
        return {
            (f"sheet-{index}.csv", f"Figure {index}"):
                _CountingGrid({
                    (row, 0): value
                    for row, value in enumerate((
                        zero,
                        2.25,
                        7.5,
                        4.125,
                        2.25,
                        7.5,
                    ))
                })
            for index, zero in enumerate(zeros)
        }

    baseline_grids = build_grids((0.0, 0.0, 0.0))
    signed_grids = build_grids((0.0, -0.0, 0.0))
    assert len({
        frozenset(grid.values())
        for grid in signed_grids.values()
    }) == 1

    baseline_axis, baseline_coverage = audit._axis_columns(
        baseline_grids, with_coverage=True
    )
    signed_axis, signed_coverage = audit._axis_columns(
        signed_grids, with_coverage=True
    )

    expected_axis = {key: {0} for key in signed_grids}
    assert baseline_axis == expected_axis
    assert signed_axis == expected_axis
    assert signed_coverage == baseline_coverage
    assert signed_coverage["axis_recurrence_mark_visits"] == 3
    assert signed_coverage["axis_context_available"] is True
    for grids in (baseline_grids, signed_grids):
        assert all(
            grid.item_visits == len(grid)
            for grid in grids.values()
        )


def _axis_finalization_grids():
    axis_values = (
        1.125,
        4.875,
        2.250,
        7.625,
        1.125,
        4.875,
    )
    support = {
        (row, 0): value
        for row, value in enumerate(axis_values[:4])
    }
    left = {}
    right = {}
    for row, value in enumerate(axis_values):
        left[(row, 0)] = value
        right[(row, 0)] = value
        left[(row, 1)] = 100 + row + 0.125
        right[(row, 1)] = 200 + row + 0.375
    return {
        ("support.csv", "support"): support,
        ("left.csv", "Figure 1"): left,
        ("right.csv", "Figure 2"): right,
    }


def test_axis_compact_finalization_matches_concrete_processing(
    monkeypatch
):
    comparison_calls = 0
    progression_cells = 0
    original_equal = audit._axis_payload_equal
    original_progression = audit._is_axis_progression_arrays

    def tracked_equal(*args, **kwargs):
        nonlocal comparison_calls
        comparison_calls += 1
        return original_equal(*args, **kwargs)

    def tracked_progression(rows, values, **kwargs):
        nonlocal progression_cells
        progression_cells += len(values)
        return original_progression(rows, values, **kwargs)

    monkeypatch.setattr(
        audit, "_axis_payload_equal", tracked_equal
    )
    monkeypatch.setattr(
        audit,
        "_is_axis_progression_arrays",
        tracked_progression,
    )

    _axis, coverage = audit._axis_columns(
        _axis_finalization_grids(),
        with_coverage=True,
    )

    assert progression_cells == coverage[
        "axis_progression_visits"
    ]
    assert comparison_calls == coverage[
        "axis_recurrence_comparison_visits"
    ]
    assert coverage["axis_recurrence_order_visits"] == 5
    assert coverage["axis_recurrence_group_visits"] == 5
    assert coverage["axis_recurrence_comparison_visits"] == 5
    assert coverage["axis_recurrence_mark_visits"] == 3
    assert coverage["axis_output_visits"] == 5
    assert coverage["axis_work_skipped_lower_bound"] == 0
    assert coverage["axis_work_skipped_is_lower_bound"] is False


def test_axis_comparison_budget_stops_before_uncharged_compare(
    monkeypatch
):
    baseline, baseline_coverage = audit._axis_columns(
        _axis_finalization_grids(),
        with_coverage=True,
    )
    comparison_calls = 0
    original_equal = audit._axis_payload_equal

    def tracked_equal(*args, **kwargs):
        nonlocal comparison_calls
        comparison_calls += 1
        return original_equal(*args, **kwargs)

    monkeypatch.setattr(
        audit, "_axis_payload_equal", tracked_equal
    )
    before_comparisons = (
        baseline_coverage["axis_value_visits"]
        - baseline_coverage["axis_recurrence_comparison_visits"]
        - baseline_coverage["axis_recurrence_mark_visits"]
        - baseline_coverage["axis_output_visits"]
    )
    budget = CrossSheetWorkBudget(
        pair_limit=100,
        value_limit=before_comparisons + 2,
        tail_match_limit=100,
        finding_limit=100,
    )

    limited, coverage = audit._axis_columns(
        _axis_finalization_grids(),
        budget=budget,
        with_coverage=True,
    )

    assert baseline
    assert limited == {}
    assert comparison_calls == 2
    assert coverage["axis_recurrence_comparison_visits"] == 2
    assert coverage["axis_recurrence_mark_visits"] == 0
    assert coverage["axis_output_visits"] == 0
    assert coverage["axis_work_skipped_lower_bound"] > 0
    assert coverage["axis_work_skipped_is_lower_bound"] is True
    assert budget.values_examined == before_comparisons + 2


def test_axis_output_budget_rejects_before_mapping_traversal():
    _baseline, baseline_coverage = audit._axis_columns(
        _axis_finalization_grids(),
        with_coverage=True,
    )
    output_visits = baseline_coverage["axis_output_visits"]
    before_output = (
        baseline_coverage["axis_value_visits"] - output_visits
    )
    budget = CrossSheetWorkBudget(
        pair_limit=100,
        value_limit=before_output + output_visits - 1,
        tail_match_limit=100,
        finding_limit=100,
    )

    axis, coverage = audit._axis_columns(
        _axis_finalization_grids(),
        budget=budget,
        with_coverage=True,
    )

    assert axis == {}
    assert coverage["axis_output_visits"] == 0
    assert coverage["axis_work_skipped_lower_bound"] == output_visits
    assert coverage["axis_work_skipped_is_lower_bound"] is False
    assert budget.values_examined == before_output


def test_axis_fingerprint_keeps_existing_four_unique_value_floor():
    grids = {
        (f"{index}.csv", f"Figure {index}"): {
            (0, 0): 1.125,
            (2, 0): 4.875,
            (5, 0): 2.250,
            (0, 1): 10.125 + index,
            (2, 1): 14.875 + index,
            (5, 1): 12.250 + index,
        }
        for index in range(3)
    }

    axis = audit._axis_columns(grids)

    assert all(0 not in columns for columns in axis.values())


def test_four_cell_recurrence_support_preserves_axis_downgrade():
    axis_values = (
        1.125,
        4.875,
        2.250,
        7.625,
        1.125,
        4.875,
    )
    support = _CountingGrid({
        (row, 0): value
        for row, value in enumerate(axis_values[:4])
    })
    left = {}
    right = {}
    for row, value in enumerate(axis_values):
        left[(row, 0)] = value
        right[(row, 0)] = value
        left[(row, 1)] = 100 + row + 0.125
        right[(row, 1)] = 200 + row + 0.375
    grids = {
        ("support.csv", "support"): support,
        ("left.csv", "Figure 1"): left,
        ("right.csv", "Figure 2"): right,
    }

    findings = detect_collisions(grids)
    finding = _find(
        findings,
        "cross_sheet_position_identical",
    )

    assert finding is not None
    assert {
        finding["file_a"],
        finding["file_b"],
    } == {"left.csv", "right.csv"}
    assert finding["axis_overlap"] is True
    assert finding["severity"] == "low"
    assert support.item_visits == len(support)


@pytest.mark.parametrize(
    "helper_name",
    [
        "_cross_sheet_pair_stats",
        "_detect_decimal_tail_reuse_for_pair",
    ],
)
@pytest.mark.parametrize("blocked_limit", ["pair", "value"])
def test_pair_helpers_reject_before_source_grid_access(
    helper_name, blocked_limit
):
    left = _VisitGrid(_sized_grid(9))
    right = _VisitGrid(_sized_grid(8, 100))
    candidate_value_count = len(left) + len(right)
    budget = CrossSheetWorkBudget(
        pair_limit=0 if blocked_limit == "pair" else 1,
        value_limit=(
            candidate_value_count - 1
            if blocked_limit == "value"
            else candidate_value_count
        ),
        tail_match_limit=100,
        finding_limit=100,
    )

    result, coverage = getattr(audit, helper_name)(
        left,
        right,
        budget=budget,
        with_coverage=True,
    )

    assert result is None
    assert coverage["pair_admitted"] is False
    assert coverage["candidate_value_count"] == candidate_value_count
    assert coverage["value_visits"] == 0
    assert left.value_visits == 0
    assert right.value_visits == 0
    metadata = budget.limitation_metadata()
    assert metadata["pairs_examined"] == 0
    assert metadata["pairs_skipped"] == 1
    assert metadata["values_examined"] == 0
    assert metadata["values_skipped"] == candidate_value_count
    assert metadata["limits_reached"] == [blocked_limit]


@pytest.mark.parametrize(
    "helper_name",
    [
        "_cross_sheet_pair_stats",
        "_detect_decimal_tail_reuse_for_pair",
    ],
)
def test_pair_helpers_own_exact_completed_work(helper_name):
    left = _VisitGrid(_sized_grid(9))
    right = _VisitGrid(_sized_grid(8, 100))
    candidate_value_count = len(left) + len(right)
    budget = CrossSheetWorkBudget(
        pair_limit=1,
        value_limit=candidate_value_count,
        tail_match_limit=100,
        finding_limit=100,
    )

    _result, coverage = getattr(audit, helper_name)(
        left,
        right,
        budget=budget,
        with_coverage=True,
    )

    assert coverage["pair_admitted"] is True
    assert coverage["candidate_value_count"] == candidate_value_count
    assert coverage["value_visits"] == candidate_value_count
    assert left.value_visits == len(left)
    assert right.value_visits == len(right)
    metadata = budget.limitation_metadata()
    assert metadata["pairs_examined"] == 1
    assert metadata["pairs_skipped"] == 0
    assert metadata["values_examined"] == candidate_value_count
    assert metadata["values_skipped"] == 0


@pytest.mark.parametrize(
    (
        "value_limit",
        "expected_stage_visits",
        "expected_grid_visits",
        "expected_examined",
        "expected_skipped",
    ),
    [
        (7, (0, 0, 0, 0), 0, 0, 48),
        (63, (16, 16, 16, 8), 16, 56, 14),
    ],
)
def test_axis_work_stops_before_the_rejected_stage(
    value_limit,
    expected_stage_visits,
    expected_grid_visits,
    expected_examined,
    expected_skipped,
):
    grids = {
        ("a.csv", "Figure 1"): _CountingGrid({
            (row, 0): row + 0.125 for row in range(8)
        }),
        ("b.csv", "Figure 2"): _CountingGrid({
            (row, 0): row + 10.375 for row in range(8)
        }),
    }
    budget = CrossSheetWorkBudget(
        pair_limit=100,
        value_limit=value_limit,
        tail_match_limit=100,
        finding_limit=100,
    )

    axis, coverage = audit._axis_columns(
        grids, budget=budget, with_coverage=True
    )

    assert axis == {}
    assert sum(grid.item_visits for grid in grids.values()) == (
        expected_grid_visits
    )
    assert coverage["axis_context_available"] is False
    assert coverage["axis_state_unit_limit"] == (
        audit._AXIS_STATE_UNITS_PER_CELL * 16
    )
    assert coverage["axis_peak_state_units"] <= (
        coverage["axis_state_unit_limit"]
    )
    assert (
        coverage["axis_loading_visits"],
        coverage["axis_grouping_visits"],
        coverage["axis_progression_visits"],
        coverage["axis_fingerprint_visits"],
    ) == expected_stage_visits
    assert coverage["axis_recurrence_order_visits"] == 0
    assert coverage["axis_recurrence_group_visits"] == 0
    assert coverage["axis_recurrence_comparison_visits"] == 0
    assert coverage["axis_recurrence_mark_visits"] == 0
    assert coverage["axis_output_visits"] == 0
    assert coverage["axis_value_visits"] == sum(expected_stage_visits)
    assert coverage["axis_work_skipped_lower_bound"] == (
        expected_skipped
    )
    assert coverage["axis_work_skipped_is_lower_bound"] is True
    assert budget.values_examined == expected_examined
    assert budget.values_skipped == expected_skipped


def test_axis_state_rejection_at_fingerprint_after_grouping_counts_known_work(
    monkeypatch,
):
    grids = {
        ("a.csv", "Figure 1"): _CountingGrid({
            (row, 0): row + 0.125 for row in range(8)
        }),
        ("b.csv", "Figure 2"): _CountingGrid({
            (row, 0): row + 10.375 for row in range(8)
        }),
    }
    budget = CrossSheetWorkBudget(
        pair_limit=100,
        value_limit=10_000,
        tail_match_limit=100,
        finding_limit=100,
    )
    original_try_reserve = audit.StateBudget.try_reserve
    rejected_names = []

    def reject_fingerprint_state(state, name, units):
        if name == "axis_unique_values":
            rejected_names.append(name)
            return None
        return original_try_reserve(state, name, units)

    monkeypatch.setattr(
        audit.StateBudget,
        "try_reserve",
        reject_fingerprint_state,
    )

    axis, coverage = audit._axis_columns(
        grids,
        budget=budget,
        with_coverage=True,
    )

    assert axis == {}
    assert rejected_names == ["axis_unique_values"]
    assert sum(grid.item_visits for grid in grids.values()) == 8
    assert coverage["axis_context_available"] is False
    assert (
        coverage["axis_loading_visits"],
        coverage["axis_grouping_visits"],
        coverage["axis_progression_visits"],
        coverage["axis_fingerprint_visits"],
    ) == (8, 8, 8, 0)
    assert coverage["axis_recurrence_order_visits"] == 0
    assert coverage["axis_recurrence_group_visits"] == 0
    assert coverage["axis_output_visits"] == 0
    assert coverage["axis_value_visits"] == 24
    assert coverage["axis_work_skipped_lower_bound"] == 35
    assert coverage["axis_work_skipped_is_lower_bound"] is True
    assert budget.values_examined == 24
    assert budget.values_skipped == 35


def test_axis_zero_support_has_zero_state_budget():
    grids = {
        ("a.csv", "Figure 1"): {
            (row, 0): row + 0.125 for row in range(3)
        },
        ("b.csv", "Figure 2"): {
            (row, 0): row + 10.375 for row in range(3)
        },
    }

    axis, coverage = audit._axis_columns(
        grids,
        with_coverage=True,
    )

    assert axis == {}
    assert coverage["recurrence_support_cells"] == 0
    assert coverage["axis_value_visits"] == 0
    assert coverage["axis_state_unit_limit"] == 0
    assert coverage["axis_peak_state_units"] == 0


def test_axis_state_rejection_precedes_grid_loading():
    grids = {
        ("a.csv", "Figure 1"): _CountingGrid({
            (row, 0): row + 0.125 for row in range(8)
        }),
        ("b.csv", "Figure 2"): _CountingGrid({
            (row, 0): row + 10.375 for row in range(8)
        }),
    }
    budget = CrossSheetWorkBudget(
        pair_limit=100,
        value_limit=10_000,
        tail_match_limit=100,
        finding_limit=100,
    )

    axis, coverage = audit._axis_columns(
        grids,
        budget=budget,
        with_coverage=True,
        _state_limit=0,
    )

    assert axis == {}
    assert sum(grid.item_visits for grid in grids.values()) == 0
    assert coverage["axis_context_available"] is False
    assert coverage["axis_value_visits"] == 0
    assert coverage["axis_state_unit_limit"] == 0
    assert coverage["axis_peak_state_units"] == 0
    assert coverage["axis_work_skipped_lower_bound"] == 48
    assert coverage["axis_work_skipped_is_lower_bound"] is True
    assert budget.values_examined == 0
    assert budget.values_skipped == 48
    assert budget.limitation_metadata()["limits_reached"] == [
        "axis"
    ]


def test_axis_state_multiplier_covers_many_column_worst_case(
    monkeypatch
):
    grids = {
        (f"{sheet}.csv", f"Figure {sheet}"): {
            (row, column): row + 0.125
            for row in range(4)
            for column in range(64)
        }
        for sheet in range(3)
    }
    expected_names = {
        "axis_column_table",
        "axis_fingerprint_payloads",
        "axis_records",
        "axis_order",
        "axis_sort_workspace",
        "axis_ordered_records",
        "axis_unique_workspace",
        "axis_unique_values",
        "axis_canonical_values",
        "axis_fingerprint_temp",
        "axis_fingerprint_order",
        "axis_fingerprint_order_workspace",
        "axis_output_capacity",
    }
    seen_names = set()
    states = []
    original = audit.StateBudget.try_reserve

    def tracked_reserve(state, name, units):
        if state not in states:
            states.append(state)
        seen_names.add(name.split(":", 1)[0])
        return original(state, name, units)

    monkeypatch.setattr(
        audit.StateBudget, "try_reserve", tracked_reserve
    )
    baseline, baseline_coverage = audit._axis_columns(
        grids,
        with_coverage=True,
        _state_limit=10_000_000,
    )
    required = baseline_coverage["axis_peak_state_units"]
    cell_count = sum(len(grid) for grid in grids.values())
    default_limit = audit._AXIS_STATE_UNITS_PER_CELL * cell_count

    assert expected_names <= seen_names
    assert baseline_coverage["axis_state_unit_limit"] == 10_000_000
    assert 0 < required <= default_limit
    assert all(state.live_units == 0 for state in states)

    limited, limited_coverage = audit._axis_columns(
        grids,
        with_coverage=True,
        _state_limit=required - 1,
    )
    assert limited == {}
    assert limited_coverage["axis_context_available"] is False
    assert limited_coverage["axis_state_unit_limit"] == required - 1
    assert limited_coverage["axis_peak_state_units"] <= required - 1

    exact, exact_coverage = audit._axis_columns(
        grids,
        with_coverage=True,
        _state_limit=required,
    )
    assert exact == baseline
    assert exact_coverage["axis_context_available"] is True
    assert exact_coverage["axis_state_unit_limit"] == required
    assert exact_coverage["axis_peak_state_units"] == required

    default, default_coverage = audit._axis_columns(
        grids,
        with_coverage=True,
    )
    assert default == baseline
    assert default_coverage["axis_context_available"] is True
    assert default_coverage["axis_state_unit_limit"] == default_limit
    assert default_coverage["axis_peak_state_units"] == required
    assert all(state.live_units == 0 for state in states)
